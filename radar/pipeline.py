"""Orquesta la ingesta: estimación, Apify, outliers, transcripción y análisis.

`radar run` muestra la estimación antes de lanzar. Sin `--yes` no llama a Apify.
Sin `APIFY_TOKEN` tampoco. Los tests pasan un cliente falso y no abren la red.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from radar.apify_client import ApifyClient, ApifyError, ApifyQuotaError, MissingApifyToken
from radar.apify_parse import ParsedReel, parse_payload
from radar.config import Settings
from radar.cost import CostEstimate, estimate_cost
from radar.db import Database
from radar.format_es import format_usd
from radar.llm import AnalysisDraft, ChatClient, LLMError, analyze_with_client
from radar.llm_codex import CodexNotInstalled, CodexNotLoggedIn, CodexRateLimit
from radar.carrusel import completar_diapositivas, texto_unido
from radar.hooks import first_sentence
from radar.ocr import OcrNotInstalled, leer_imagen
from radar.sources.instaloader_source import (
    InstagramBlocked,
    InstagramNotConfigured,
    InstaloaderSource,
)
from radar.sources.playwright_source import (
    BrowserBlocked,
    BrowserNotConfigured,
    PlaywrightSource,
)
from radar.sources.select import SourcePlan, plan_source
from radar.transcribe import (
    DownloadError,
    WhisperNotInstalled,
    choose_media_url,
    download_media,
    transcribe_file,
)


class NoActiveAccounts(RuntimeError):
    """La watchlist no tiene cuentas activas."""


class OverBudget(RuntimeError):
    def __init__(self, estimate: CostEstimate) -> None:
        self.estimate = estimate
        super().__init__(
            "La estimación de "
            f"{format_usd(estimate.estimated_usd)} supera el crédito de referencia de "
            f"{format_usd(estimate.monthly_credit_usd)}. No se ha llamado a Apify. "
            "Repite el comando con --allow-over-budget solo si aceptas ese coste."
        )


@dataclass
class RunOutcome:
    launched: bool
    estimate: CostEstimate
    needs_confirmation: bool = False
    run_id: int | None = None
    reels_saved: int = 0
    status: str = "planned"
    message: str = ""
    source: str = ""


def plan_run(
    db: Database,
    settings: Settings,
    *,
    reels_per_account: int | None = None,
    allow_over_budget: bool = False,
    yes: bool = False,
    dry_run: bool = False,
) -> tuple[CostEstimate, list[str]]:
    usernames = db.list_active_usernames()
    if not usernames:
        raise NoActiveAccounts("No hay cuentas activas en la watchlist.")
    limit = reels_per_account or settings.reels_per_account
    if limit < 1:
        raise ValueError("Pide al menos 1 reel por cuenta.")
    estimate = estimate_cost(
        len(usernames),
        limit,
        usd_per_1000=settings.usd_per_1000,
        monthly_credit_usd=settings.monthly_credit_usd,
    )
    if dry_run or not yes:
        return estimate, usernames
    if estimate.over_budget and not allow_over_budget:
        raise OverBudget(estimate)
    return estimate, usernames


def execute_run(
    db: Database,
    settings: Settings,
    *,
    client: ApifyClient | None = None,
    reels_per_account: int | None = None,
    allow_over_budget: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    skip_transcribe: bool = False,
    skip_llm: bool = False,
    chat_client: ChatClient | None = None,
) -> RunOutcome:
    estimate, usernames = plan_run(
        db,
        settings,
        reels_per_account=reels_per_account,
        allow_over_budget=allow_over_budget,
        yes=yes,
        dry_run=dry_run,
    )
    if dry_run or not yes:
        return RunOutcome(
            launched=False,
            estimate=estimate,
            needs_confirmation=not dry_run,
            message="No se ha llamado a Apify.",
        )

    limit = estimate.reels_per_account
    owns_client = False
    if client is None:
        if not settings.apify_token:
            raise MissingApifyToken("Falta APIFY_TOKEN. No se ha hecho ninguna petición.")
        client = ApifyClient(settings.apify_token, actor=settings.apify_actor)
        owns_client = True

    run_id = db.create_run(reels_requested=estimate.reels_total, estimated_usd=str(estimate.estimated_usd))
    try:
        items = client.fetch_reels(usernames, limit)
        parsed = parse_payload(items)
        saved = ingest_reels(db, parsed.reels, is_example=False, run_id=run_id, settings=settings)
        notes = [f"Guardados {saved} reels."]
        if parsed.errors:
            notes.append(f"{len(parsed.errors)} elementos no se pudieron leer.")
        notes.extend(
            _pasos_posteriores(
                db,
                settings,
                skip_transcribe=skip_transcribe,
                skip_llm=skip_llm,
                chat_client=chat_client,
                conservar_imagenes=False,
                ocr=None,
                image_downloader=None,
            )
        )
        message = " ".join(notes)
        db.finish_run(run_id, status="succeeded", reels_saved=saved, message=message)
        return RunOutcome(
            launched=True,
            estimate=estimate,
            run_id=run_id,
            reels_saved=saved,
            status="succeeded",
            message=message,
        )
    except (ApifyError, MissingApifyToken) as exc:
        db.finish_run(run_id, status="failed", reels_saved=0, message=str(exc))
        raise
    except Exception as exc:
        db.finish_run(run_id, status="failed", reels_saved=0, message=str(exc))
        raise
    finally:
        if owns_client:
            client.close()


def execute_selected_run(
    db: Database,
    settings: Settings,
    *,
    client: ApifyClient | None = None,
    instaloader_source: InstaloaderSource | None = None,
    reels_per_account: int | None = None,
    allow_over_budget: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    skip_transcribe: bool = False,
    skip_llm: bool = False,
    chat_client: ChatClient | None = None,
    requested_source: str | None = None,
    browser_source: PlaywrightSource | None = None,
    conservar_imagenes: bool = False,
    ocr=None,
    image_downloader=None,
) -> RunOutcome:
    """Elige la fuente y lanza. Apify solo si hay token y el coste cabe.

    Un 402 o un error de cuota cae a Instaloader una vez, sin reintentar Apify.
    """
    usernames = db.list_active_usernames()
    if not usernames:
        raise NoActiveAccounts("No hay cuentas activas en la watchlist.")
    limit = reels_per_account or settings.reels_per_account
    plan = plan_source(
        settings,
        usernames,
        limit,
        requested=requested_source,
        allow_over_budget=allow_over_budget,
    )
    if dry_run or not yes:
        return RunOutcome(
            launched=False,
            estimate=plan.estimate,
            needs_confirmation=not dry_run,
            message="No se ha hecho ninguna petición.",
            source=plan.effective,
        )
    if plan.effective == "playwright":
        return _execute_browser(
            db,
            settings,
            plan,
            source=browser_source,
            skip_transcribe=skip_transcribe,
            skip_llm=skip_llm,
            chat_client=chat_client,
            conservar_imagenes=conservar_imagenes,
            ocr=ocr,
            image_downloader=image_downloader,
        )
    if plan.effective == "instaloader":
        return _execute_instaloader(
            db,
            settings,
            plan,
            source=instaloader_source,
            skip_transcribe=skip_transcribe,
            skip_llm=skip_llm,
            chat_client=chat_client,
        )
    try:
        outcome = execute_run(
            db,
            settings,
            client=client,
            reels_per_account=limit,
            allow_over_budget=allow_over_budget,
            yes=True,
            skip_transcribe=skip_transcribe,
            skip_llm=skip_llm,
            chat_client=chat_client,
        )
    except ApifyQuotaError as exc:
        return _execute_instaloader(
            db,
            settings,
            plan_source(
                settings,
                usernames,
                limit,
                requested="instaloader",
            ),
            source=instaloader_source,
            skip_transcribe=skip_transcribe,
            skip_llm=skip_llm,
            chat_client=chat_client,
            note=(
                "Apify respondió sin crédito o sin cuota "
                f"({exc}). No se reintenta Apify. Se usa Instaloader."
            ),
        )
    outcome.source = "apify"
    return outcome


def _execute_instaloader(
    db: Database,
    settings: Settings,
    plan: SourcePlan,
    *,
    source: InstaloaderSource | None,
    skip_transcribe: bool,
    skip_llm: bool,
    chat_client: ChatClient | None,
    note: str = "",
) -> RunOutcome:
    owns = False
    if source is None:
        if not settings.ig_ready:
            message = str(InstagramNotConfigured())
            if note:
                message = f"{note} {message}"
            raise InstagramNotConfigured(message)
        source = InstaloaderSource(
            settings.ig_username,
            settings.ig_session_file,
            max_accounts=settings.ig_max_accounts,
        )
        owns = True
    run_id = db.create_run(reels_requested=plan.estimate.reels_total, estimated_usd="0.00")
    try:
        try:
            items = source.fetch_reels(plan.usernames, plan.estimate.reels_per_account)
        except InstagramBlocked as exc:
            saved = _save_parsed(db, exc.partial, run_id=run_id, settings=settings)
            message = _instaloader_message(note, saved, source, extra=str(exc))
            db.finish_run(run_id, status="failed", reels_saved=saved, message=message)
            return RunOutcome(
                launched=True,
                estimate=plan.estimate,
                run_id=run_id,
                reels_saved=saved,
                status="failed",
                message=message,
                source="instaloader",
            )
        saved = _save_parsed(db, items, run_id=run_id, settings=settings)
        notes = [part for part in (note, f"Fuente instaloader. Guardados {saved} reels.") if part]
        skipped = plan.skipped or getattr(source, "skipped", [])
        if skipped:
            notes.append(f"Cuentas fuera de esta tanda: {', '.join(skipped)}.")
        warnings = getattr(source, "warnings", [])
        if warnings:
            notes.append(" ".join(warnings))
        notes.extend(
            _pasos_posteriores(
                db,
                settings,
                skip_transcribe=skip_transcribe,
                skip_llm=skip_llm,
                chat_client=chat_client,
                conservar_imagenes=False,
                ocr=None,
                image_downloader=None,
            )
        )
        message = " ".join(notes)
        db.finish_run(run_id, status="succeeded", reels_saved=saved, message=message)
        return RunOutcome(
            launched=True,
            estimate=plan.estimate,
            run_id=run_id,
            reels_saved=saved,
            status="succeeded",
            message=message,
            source="instaloader",
        )
    except Exception as exc:
        db.finish_run(run_id, status="failed", reels_saved=0, message=str(exc))
        raise
    finally:
        if owns:
            source.close()


def _execute_browser(
    db: Database,
    settings: Settings,
    plan: SourcePlan,
    *,
    source: PlaywrightSource | None,
    skip_transcribe: bool,
    skip_llm: bool,
    chat_client: ChatClient | None,
    conservar_imagenes: bool,
    ocr,
    image_downloader,
    note: str = "",
) -> RunOutcome:
    owns = False
    if source is None:
        if not settings.browser_ready:
            message = str(BrowserNotConfigured())
            if note:
                message = f"{note} {message}"
            raise BrowserNotConfigured(message)
        source = PlaywrightSource(
            settings.browser_profile,
            max_accounts=settings.browser_max_accounts,
            max_posts=settings.browser_max_posts,
            account_delay=settings.browser_pause_account,
            post_delay=settings.browser_pause_post,
        )
        owns = True
    run_id = db.create_run(reels_requested=plan.estimate.reels_total, estimated_usd="0.00")
    try:
        try:
            items = source.fetch_reels(plan.usernames, plan.estimate.reels_per_account)
        except BrowserBlocked as exc:
            saved = _save_parsed(db, exc.partial, run_id=run_id, settings=settings)
            message = " ".join(
                part for part in (note, f"Guardados {saved} posts antes de parar.", str(exc)) if part
            )
            db.finish_run(run_id, status="failed", reels_saved=saved, message=message)
            return RunOutcome(
                launched=True,
                estimate=plan.estimate,
                run_id=run_id,
                reels_saved=saved,
                status="failed",
                message=message,
                source="playwright",
            )
        saved = _save_parsed(db, items, run_id=run_id, settings=settings)
        notes = [part for part in (note, f"Fuente playwright. Guardados {saved} posts.") if part]
        skipped = plan.skipped or getattr(source, "skipped", [])
        if skipped:
            notes.append(f"Cuentas fuera de esta tanda: {', '.join(skipped)}.")
        warnings = getattr(source, "warnings", [])
        if warnings:
            notes.append(" ".join(warnings))
        notes.extend(
            _pasos_posteriores(
                db,
                settings,
                skip_transcribe=skip_transcribe,
                skip_llm=skip_llm,
                chat_client=chat_client,
                conservar_imagenes=conservar_imagenes,
                ocr=ocr,
                image_downloader=image_downloader,
            )
        )
        message = " ".join(notes)
        db.finish_run(run_id, status="succeeded", reels_saved=saved, message=message)
        return RunOutcome(
            launched=True,
            estimate=plan.estimate,
            run_id=run_id,
            reels_saved=saved,
            status="succeeded",
            message=message,
            source="playwright",
        )
    except Exception as exc:
        db.finish_run(run_id, status="failed", reels_saved=0, message=str(exc))
        raise
    finally:
        if owns:
            source.close()


def _pasos_posteriores(
    db: Database,
    settings: Settings,
    *,
    skip_transcribe: bool,
    skip_llm: bool,
    chat_client: ChatClient | None,
    conservar_imagenes: bool,
    ocr,
    image_downloader,
) -> list[str]:
    notes: list[str] = []
    if skip_transcribe:
        notes.append("Transcripción omitida.")
    else:
        notes.append(
            preparar_carruseles(
                db,
                settings,
                conservar=conservar_imagenes,
                ocr=ocr,
                downloader=image_downloader,
            )
        )
        notes.append(transcribe_pending(db, settings))
    if skip_llm:
        notes.append("Análisis omitido.")
    else:
        notes.append(analyze_pending(db, settings, chat_client=chat_client))
    return notes


def preparar_carruseles(
    db: Database,
    settings: Settings,
    *,
    conservar: bool = False,
    ocr=None,
    downloader=None,
) -> str:
    rows = db.carruseles_sin_transcripcion()
    if not rows:
        return "No hay carruseles pendientes de texto."
    motor = ocr or leer_imagen
    bajar = downloader or _bajar_imagen
    hechos = 0
    avisos = 0
    with tempfile.TemporaryDirectory(prefix="radar-slides-") as folder:
        raiz = Path(folder)
        for row in rows:
            slides = json.loads(row["slides_json"] or "[]")
            if not isinstance(slides, list):
                slides = []
            trabajo = raiz / str(row["id"])
            try:
                nuevos, conservados = completar_diapositivas(
                    slides,
                    ocr=motor,
                    downloader=bajar,
                    workdir=trabajo,
                    conservar=conservar,
                )
            except OcrNotInstalled as exc:
                db.set_transcript_error(row["id"], str(exc))
                avisos += 1
                continue
            if conservar and conservados:
                destino = settings.db_path.parent / "slides" / str(row["id"])
                destino.mkdir(parents=True, exist_ok=True)
                for path in conservados:
                    (destino / path.name).write_bytes(path.read_bytes())
            db.update_slides(row["id"], nuevos)
            unidos = texto_unido(nuevos)
            if not unidos:
                db.set_transcript_error(
                    row["id"],
                    "El carrusel no trae texto y el OCR no leyó nada. No se inventa el gancho.",
                )
                avisos += 1
                continue
            primero = next((slide.get("texto") or "" for slide in nuevos if slide.get("texto")), "")
            db.save_transcript(
                row["id"],
                text=unidos,
                language=settings.whisper_language,
                hook_text=first_sentence(str(primero)) or str(primero),
                segments=[],
                model="diapositivas",
            )
            hechos += 1
    if avisos and hechos == 0:
        return f"Ningún carrusel pudo leerse ({avisos})."
    return f"Carruseles con texto: {hechos}."


def _bajar_imagen(url: str, workdir: Path, indice: int) -> Path | None:
    from radar.carrusel import descargar_imagen

    return descargar_imagen(url, workdir, indice)


def _save_parsed(db: Database, items: list, *, run_id: int, settings: Settings) -> int:
    from radar.apify_parse import parse_payload

    parsed = parse_payload(items)
    return ingest_reels(db, parsed.reels, is_example=False, run_id=run_id, settings=settings)


def _instaloader_message(note: str, saved: int, source: InstaloaderSource, *, extra: str) -> str:
    parts = [part for part in (note, f"Guardados {saved} reels antes de parar.", extra) if part]
    warnings = getattr(source, "warnings", [])
    if warnings:
        parts.append(" ".join(warnings))
    return " ".join(parts)


def describe_plan(plan: SourcePlan) -> str:
    if plan.effective == "playwright":
        lines = [
            "Estimación antes de lanzar (no es un cargo):",
            "  Fuente: playwright",
            f"  Motivo: {plan.reason}",
            f"  Cuentas en esta tanda: {plan.estimate.accounts}",
            f"  Posts por cuenta: {plan.estimate.reels_per_account}",
            f"  Resultados previstos: {plan.estimate.reels_total}",
            "  Coste estimado de esta tanda: 0,00 USD",
            "  El navegador no usa el crédito de Apify.",
        ]
        if plan.skipped:
            names = ", ".join(f"@{name}" for name in plan.skipped)
            lines.append(
                f"  Fuera de esta tanda (máximo {plan.max_accounts}, orden alfabético): {names}"
            )
        lines.append(
            "  Ritmo: pausas al azar entre perfiles y entre páginas. "
            "Si Instagram responde 401, 429, checkpoint o challenge, la tanda se para y no se reintenta."
        )
        lines.append(
            "  Aviso: el scraping con sesión va contra las condiciones de Instagram "
            "y puede llevar al bloqueo de la cuenta secundaria."
        )
        lines.append(
            "  Referencia si fuera Apify: "
            f"{format_usd(plan.apify_estimate.estimated_usd)} "
            f"({format_usd(plan.apify_estimate.usd_per_1000)} / 1.000)."
        )
        return "\n".join(lines)
    if plan.effective == "instaloader":
        lines = [
            "Estimación antes de lanzar (no es un cargo):",
            "  Fuente: instaloader",
            f"  Motivo: {plan.reason}",
            f"  Cuentas en esta tanda: {plan.estimate.accounts}",
            f"  Reels por cuenta: {plan.estimate.reels_per_account}",
            f"  Resultados previstos: {plan.estimate.reels_total}",
            "  Coste estimado de esta tanda: 0,00 USD",
            "  Instaloader no usa el crédito de Apify.",
        ]
        if plan.skipped:
            names = ", ".join(f"@{name}" for name in plan.skipped)
            lines.append(
                f"  Fuera de esta tanda (máximo {plan.max_accounts}, orden alfabético): {names}"
            )
        lines.append(
            "  Referencia si fuera Apify: "
            f"{format_usd(plan.apify_estimate.estimated_usd)} "
            f"({format_usd(plan.apify_estimate.usd_per_1000)} / 1.000)."
        )
        lines.append(
            "  Riesgo: Instagram puede limitar la cuenta que inicia sesión. "
            "Usa una cuenta secundaria y, si bloquea, una IP residencial."
        )
        return "\n".join(lines)
    return f"Fuente: apify\nMotivo: {plan.reason}\n{describe_estimate(plan.estimate)}"


def ingest_reels(
    db: Database,
    reels: list[ParsedReel],
    *,
    is_example: bool,
    run_id: int | None,
    settings: Settings | None = None,
) -> int:
    saved = 0
    for reel in reels:
        username = reel.owner_username
        if not username:
            continue
        account_id = db.ensure_account(username)
        db.upsert_reel(account_id, reel, is_example=is_example, run_id=run_id)
        saved += 1
    sample_size = settings.sample_size if settings else 20
    threshold = settings.outlier_multiple if settings else 2.0
    weight = settings.comment_weight if settings else 4.0
    db.recompute_outliers(sample_size=sample_size, threshold=threshold, comment_weight=weight)
    return saved


def transcribe_pending(db: Database, settings: Settings) -> str:
    rows = db.outliers_missing_transcript()
    if not rows:
        return "No hay outliers pendientes de transcribir."
    done = 0
    whisper_missing: str | None = None
    with tempfile.TemporaryDirectory(prefix="radar-audio-") as folder:
        for row in rows:
            url = choose_media_url(row["audio_url"], row["video_url"])
            if not url:
                db.set_transcript_error(row["id"], "El actor no devolvió audioUrl ni videoUrl.")
                continue
            if whisper_missing:
                db.set_transcript_error(row["id"], whisper_missing)
                continue
            dest = Path(folder) / f"{row['instagram_id']}.media"
            try:
                download_media(url, dest)
                result = transcribe_file(
                    dest,
                    model_name=settings.whisper_model,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                    language=settings.whisper_language,
                )
            except WhisperNotInstalled as exc:
                whisper_missing = str(exc)
                db.set_transcript_error(row["id"], whisper_missing)
                continue
            except (DownloadError, OSError, RuntimeError) as exc:
                db.set_transcript_error(row["id"], str(exc))
                continue
            db.save_transcript(
                row["id"],
                text=result.text,
                language=result.language,
                hook_text=result.hook_text,
                segments=result.segments,
                model=result.model,
            )
            done += 1
    if whisper_missing and done == 0:
        return whisper_missing
    return f"Transcritos {done} outliers."


def analyze_pending(
    db: Database,
    settings: Settings,
    *,
    chat_client: ChatClient | None = None,
    sleeper=None,
) -> str:
    """Una llamada cada vez. Con Codex hay una pausa entre reels.

    Si el plan de ChatGPT llega al límite, se guardan los ya analizados y el
    resto sigue pendiente: no hay fila de análisis, así que la próxima tanda
    los retoma. No se reintenta el que chocó con el límite.
    """
    rows = db.outliers_missing_analysis()
    if not rows:
        return "No hay outliers pendientes de analizar."
    if settings.llm_provider == "none":
        return "LLM no configurado: se omite el análisis."
    if settings.llm_provider in {"openai", "anthropic"} and not settings.llm_ready and chat_client is None:
        return "Falta LLM_MODEL o la clave del proveedor: se omite el análisis."

    owns = False
    client = chat_client
    if client is None:
        client = _build_chat_client(settings)
        owns = True
    pause = sleeper or time.sleep
    delay = max(0.0, settings.llm_call_delay) if settings.llm_provider == "codex" else 0
    done = 0
    try:
        for index, row in enumerate(rows):
            if index and delay > 0:
                pause(delay)
            try:
                draft: AnalysisDraft = analyze_with_client(
                    client,
                    account=row["account"],
                    caption=row["caption"] or "",
                    transcript=row["transcript_text"] or "",
                    perfil=db.get_perfil(),
                )
            except (CodexRateLimit, CodexNotInstalled, CodexNotLoggedIn) as exc:
                pending = (
                    "Pendiente para la próxima tanda: límite de uso de ChatGPT."
                    if isinstance(exc, CodexRateLimit)
                    else str(exc)
                )
                for leftover in rows[index:]:
                    db.set_analysis_error(leftover["id"], pending)
                return f"Analizados {done}. {exc}"
            except LLMError as exc:
                db.set_analysis_error(row["id"], str(exc))
                continue
            db.save_analysis(
                row["id"],
                hook_type=draft.hook_type,
                blocks=draft.blocks,
                template=draft.template,
                adapted_script=draft.adapted_script,
                warnings=draft.warnings,
                provider=settings.llm_provider,
                model=settings.llm_model or settings.codex_model,
                raw=draft.raw,
            )
            done += 1
    finally:
        close = getattr(client, "close", None)
        if owns and close:
            close()
    return f"Analizados {done} outliers."


def describe_estimate(estimate: CostEstimate) -> str:
    return "\n".join(
        [
            "Estimación antes de lanzar (no es un cargo):",
            f"  Cuentas activas: {estimate.accounts}",
            f"  Reels por cuenta: {estimate.reels_per_account}",
            f"  Resultados previstos: {estimate.reels_total}",
            f"  Tarifa de referencia: {format_usd(estimate.usd_per_1000)} / 1.000 reels",
            f"  Coste estimado de esta tanda: {format_usd(estimate.estimated_usd)}",
            f"  Crédito mensual de referencia (plan Free): {format_usd(estimate.monthly_credit_usd)}",
            f"  Tandas como esta que caben en ese crédito: {estimate.runs_fitting_in_credit}",
            f"  Proyección de 4 tandas al mes: {format_usd(estimate.monthly_projection_usd)}",
            "  Add-ons de Apify apagados: transcripción, descarga de vídeo y compartidos.",
        ]
    )


def _build_chat_client(settings: Settings) -> ChatClient:
    from radar.llm_codex import client_for_settings

    return client_for_settings(settings)
