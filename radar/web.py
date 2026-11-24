"""Interfaz web para revisar el banco, la watchlist y la estimación de coste."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from radar.config import Settings, load_settings
from radar.db import BankEntry, Database
from radar.export_json import dumps_document, export_document, filter_entries
from radar.export_md import render_markdown
from radar.format_es import format_date, format_int_es, format_multiple, format_usd
from radar.pipeline import NoActiveAccounts, execute_selected_run
from radar.sources.select import block_reason, plan_source
from radar.seed import seed_if_needed
from radar.tubealfred import TubeAlfredAdapter
from radar.usernames import UsernameError, normalize_username

ROOT = Path(__file__).resolve().parent
PALETTE = ("#0f766e", "#9a3412", "#1d4ed8", "#6d28d9", "#3f6212", "#b45309")
_RUN_LOCK = threading.Lock()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    seed_if_needed(db)
    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["es_int"] = format_int_es
    env.filters["es_date"] = format_date
    env.filters["usd"] = format_usd
    env.filters["multiple"] = _multiple
    env.filters["gaps"] = _gaps
    env.filters["heat"] = _heat

    app = FastAPI(title="Radar de ganchos", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    def render(name: str, **context) -> HTMLResponse:
        status = int(context.pop("status_code", 200))
        context.setdefault("nav", "")
        context.setdefault("aviso", "")
        html = env.get_template(name).render(**context)
        return HTMLResponse(html, status_code=status)

    def bank_filters(params) -> dict:
        min_raw = (params.get("min") or "").strip().replace(",", ".")
        try:
            min_multiple = float(min_raw) if min_raw else None
        except ValueError:
            min_multiple = None
        return {
            "account": (params.get("cuenta") or "").strip() or None,
            "hook_type": (params.get("tipo") or "").strip() or None,
            "min_multiple": min_multiple,
            "date_from": (params.get("desde") or "").strip() or None,
            "date_to": (params.get("hasta") or "").strip() or None,
            "hide_examples": params.get("ocultar_ejemplos") == "1",
        }

    def selected_bank(params) -> list[BankEntry]:
        return filter_entries(db.list_bank(), **bank_filters(params))

    @app.get("/salud")
    def salud() -> dict:
        return {"ok": True}

    @app.get("/")
    def inicio() -> RedirectResponse:
        return RedirectResponse("/banco", status_code=303)

    @app.get("/banco", response_class=HTMLResponse)
    def banco(request: Request) -> HTMLResponse:
        entries = selected_bank(request.query_params)
        all_entries = db.list_bank()
        types = sorted({entry.hook_type for entry in all_entries if entry.hook_type})
        accounts = sorted({entry.account for entry in all_entries})
        return render(
            "banco.html",
            nav="banco",
            title="Banco de ganchos",
            entries=entries,
            total=len(all_entries),
            types=types,
            accounts=accounts,
            filtros=request.query_params,
            hay_ejemplos=any(entry.is_example for entry in all_entries),
            color=_color,
            query=request.url.query,
        )

    @app.get("/banco/{reel_id}", response_class=HTMLResponse)
    def ficha(reel_id: int) -> HTMLResponse:
        entry = db.get_bank_entry(reel_id)
        if entry is None:
            return render(
                "vacio.html",
                title="No está en el banco",
                heading="Ese reel no está en el banco",
                text="Solo entran los que llegan al menos a 2× la mediana de su cuenta.",
                status_code=404,
            )
        return render("ficha.html", nav="banco", title=entry.hook or entry.account, entry=entry, color=_color)

    @app.get("/reels", response_class=HTMLResponse)
    def reels() -> HTMLResponse:
        return render("reels.html", nav="reels", title="Reels", rows=db.list_reels())

    @app.get("/cuentas", response_class=HTMLResponse)
    def cuentas(aviso: str = "") -> HTMLResponse:
        return render(
            "cuentas.html",
            nav="cuentas",
            title="Cuentas",
            rows=db.list_accounts(),
            aviso=aviso,
        )

    @app.post("/cuentas")
    async def crear_cuenta(request: Request) -> Response:
        form = await request.form()
        try:
            username = normalize_username(str(form.get("username") or ""))
        except UsernameError as exc:
            return render(
                "cuentas.html",
                nav="cuentas",
                title="Cuentas",
                rows=db.list_accounts(),
                aviso=str(exc),
                status_code=400,
            )
        db.upsert_account(username, active=True)
        return RedirectResponse("/cuentas", status_code=303)

    @app.post("/cuentas/{username}/estado")
    async def estado_cuenta(username: str, request: Request) -> RedirectResponse:
        form = await request.form()
        db.set_account_active(username, str(form.get("active")) == "1")
        return RedirectResponse("/cuentas", status_code=303)

    @app.post("/cuentas/{username}/borrar")
    def borrar_cuenta(username: str) -> RedirectResponse:
        db.delete_account(username)
        return RedirectResponse("/cuentas", status_code=303)

    @app.get("/radar", response_class=HTMLResponse)
    def radar(request: Request) -> HTMLResponse:
        return _radar_page(render, db, settings, request, aviso=request.query_params.get("aviso", ""))

    @app.post("/radar/lanzar")
    async def lanzar(request: Request) -> Response:
        form = await request.form()
        reels = _reels_param(form.get("reels"), settings.reels_per_account)
        allow = form.get("allow_over_budget") == "1"
        requested = str(form.get("source") or "auto")
        if form.get("confirmo") != "1":
            return _radar_page(
                render,
                db,
                settings,
                request,
                reels=reels,
                requested=requested,
                error="Marca que has visto la estimación. No se ha hecho ninguna petición.",
                status_code=400,
            )
        usernames = db.list_active_usernames()
        if not usernames:
            return _radar_page(
                render,
                db,
                settings,
                request,
                reels=reels,
                requested=requested,
                error="No hay cuentas activas en la watchlist.",
                status_code=400,
            )
        try:
            plan = plan_source(
                settings,
                usernames,
                reels,
                requested=requested,
                allow_over_budget=allow,
            )
        except (NoActiveAccounts, ValueError) as exc:
            return _radar_page(
                render,
                db,
                settings,
                request,
                reels=reels,
                requested=requested,
                error=str(exc),
                status_code=400,
            )
        blocked = block_reason(settings, plan, allow_over_budget=allow)
        if blocked:
            return _radar_page(
                render,
                db,
                settings,
                request,
                reels=reels,
                requested=requested,
                error=blocked,
                status_code=400,
            )
        if not _RUN_LOCK.acquire(blocking=False):
            return _radar_page(
                render,
                db,
                settings,
                request,
                reels=reels,
                requested=requested,
                error="Ya hay una ingesta en curso.",
                status_code=409,
            )

        def worker() -> None:
            try:
                execute_selected_run(
                    db,
                    settings,
                    reels_per_account=reels,
                    allow_over_budget=allow,
                    yes=True,
                    requested_source=requested,
                )
                db.set_meta("last_error", "")
            except Exception as exc:  # noqa: BLE001
                db.set_meta("last_error", str(exc))
            finally:
                _RUN_LOCK.release()

        threading.Thread(target=worker, name="radar-run", daemon=True).start()
        return RedirectResponse("/radar?aviso=Ingesta+lanzada.+Esta+página+se+actualiza+sola+mientras+dure.", status_code=303)

    @app.get("/ajustes", response_class=HTMLResponse)
    def ajustes() -> HTMLResponse:
        tube = TubeAlfredAdapter(settings.tubealfred_api_key)
        return render(
            "ajustes.html",
            nav="ajustes",
            title="Ajustes",
            settings=settings,
            token_listo=bool(settings.apify_token),
            llm_listo=settings.llm_ready,
            tube=tube,
            ig_listo=settings.ig_ready,
        )

    @app.get("/export/banco.md")
    def exportar_md(request: Request) -> PlainTextResponse:
        text = render_markdown(selected_bank(request.query_params))
        return PlainTextResponse(
            text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=banco-ganchos.md"},
        )

    @app.get("/export/banco.json")
    def exportar_json_banco(request: Request) -> Response:
        entries = selected_bank(request.query_params)
        urls = {entry.url for entry in entries}
        document = export_document(db)
        document["hooks"] = [hook for hook in document["hooks"] if hook.get("url") in urls]
        document["reels"] = [reel for reel in document["reels"] if reel.get("url") in urls]
        document["note"] = "Exportación filtrada del banco. Puede incluir filas de ejemplo."
        return Response(dumps_document(document), media_type="application/json; charset=utf-8")

    @app.get("/export/copia.json")
    def exportar_copia() -> Response:
        return Response(dumps_document(export_document(db)), media_type="application/json; charset=utf-8")

    return app


def _radar_page(
    render,
    db: Database,
    settings: Settings,
    request: Request,
    *,
    reels: int | None = None,
    requested: str | None = None,
    error: str = "",
    aviso: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    chosen = reels if reels is not None else _reels_param(request.query_params.get("reels"), settings.reels_per_account)
    source_requested = requested or str(request.query_params.get("source") or settings.radar_source or "auto")
    usernames = db.list_active_usernames()
    plan = None
    plan_error = ""
    if usernames:
        try:
            plan = plan_source(settings, usernames, chosen, requested=source_requested)
        except ValueError as exc:
            plan_error = str(exc)
    else:
        plan_error = "No hay cuentas activas en la watchlist."
    puede_lanzar = plan is not None and block_reason(settings, plan, allow_over_budget=True) is None
    shown_source = source_requested if source_requested in {"auto", "apify", "instaloader"} else "auto"
    running = db.running_run()
    return render(
        "radar.html",
        nav="radar",
        title="Lanzar radar",
        estimate=plan.apify_estimate if plan else None,
        plan=plan,
        usernames=usernames,
        reels=chosen,
        source_requested=shown_source,
        error=error or plan_error,
        aviso=aviso,
        token_listo=bool(settings.apify_token),
        puede_lanzar=puede_lanzar,
        running=running,
        runs=db.list_runs(),
        last_error=db.get_meta("last_error") or "",
        status_code=status_code,
    )


def _reels_param(value, default: int) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return default
    return min(50, max(1, number))


def _multiple(value) -> str:
    if value is None:
        return "—"
    return format_multiple(value)


def _gaps(value: str | None) -> Markup:
    escaped = escape(value or "")
    return Markup(str(escaped).replace("[X]", '<mark class="gap">[X]</mark>'))


def _heat(value) -> str:
    if value is None:
        return "low"
    if value >= 5:
        return "high"
    if value >= 2:
        return "mid"
    return "low"


def _color(username: str) -> str:
    total = sum(username.encode("utf-8"))
    return PALETTE[total % len(PALETTE)]
