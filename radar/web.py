"""Interfaz web para revisar el banco, la watchlist y la estimación de coste."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from radar.categorias import CATEGORIAS
from radar.config import Settings, load_settings
from radar.db import BankEntry, Database
from radar.dossier import construir_dossier
from radar.export_json import dumps_document, export_document, filter_entries
from radar.export_md import render_markdown
from radar.flujo import (
    ETIQUETAS,
    PASOS,
    FlujoError,
    aplicar_guion,
    aplicar_hook,
    aplicar_oferta,
    aplicar_referencias,
    aplicar_revision,
    aplicar_variantes,
    cargar_proyecto,
    crear_proyecto,
    exportar_proyecto,
    listar_proyectos,
)
from radar.format_es import format_date, format_int_es, format_multiple, format_usd
from radar.perfil import CAMPOS, ETIQUETAS as PERFIL_ETIQUETAS, perfil_desde_formulario
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
            "category": (params.get("categoria") or "").strip() or None,
            "media_kind": (params.get("formato") or "").strip() or None,
            "query": (params.get("q") or "").strip() or None,
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
        categorias = [nombre for nombre in CATEGORIAS if any(entry.categoria == nombre for entry in all_entries)]
        return render(
            "banco.html",
            nav="banco",
            title="Biblioteca de ganchos",
            entries=entries,
            total=len(all_entries),
            types=types,
            accounts=accounts,
            categorias=categorias,
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
        perfil = db.get_perfil()
        return render(
            "ficha.html",
            nav="banco",
            title=entry.hook or entry.account,
            entry=entry,
            dossier=construir_dossier(entry, perfil),
            perfil=perfil,
            color=_color,
        )

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

    @app.get("/perfil", response_class=HTMLResponse)
    def perfil(aviso: str = "") -> HTMLResponse:
        return render(
            "perfil.html",
            nav="perfil",
            title="Perfil",
            perfil=db.get_perfil(),
            campos=CAMPOS,
            etiquetas=PERFIL_ETIQUETAS,
            aviso=aviso,
        )

    @app.post("/perfil")
    async def guardar_perfil(request: Request) -> RedirectResponse:
        form = await request.form()
        db.save_perfil(perfil_desde_formulario(form))
        return RedirectResponse("/perfil?aviso=Perfil+guardado.", status_code=303)

    @app.get("/flujo", response_class=HTMLResponse)
    def flujo(aviso: str = "", error: str = "") -> HTMLResponse:
        return render(
            "flujo.html",
            nav="flujo",
            title="Flujo",
            proyectos=listar_proyectos(db),
            etiquetas=ETIQUETAS,
            aviso=aviso,
            error=error,
        )

    @app.post("/flujo")
    async def crear_flujo(request: Request) -> Response:
        form = await request.form()
        try:
            proyecto = crear_proyecto(db, str(form.get("titulo") or ""))
        except FlujoError as exc:
            return render(
                "flujo.html",
                nav="flujo",
                title="Flujo",
                proyectos=listar_proyectos(db),
                etiquetas=ETIQUETAS,
                error=str(exc),
                status_code=400,
            )
        return RedirectResponse(f"/flujo/{proyecto.id}", status_code=303)

    @app.get("/flujo/{proyecto_id}", response_class=HTMLResponse)
    def flujo_detalle(proyecto_id: int, aviso: str = "", error: str = "") -> HTMLResponse:
        proyecto = cargar_proyecto(db, proyecto_id)
        if proyecto is None:
            return render(
                "vacio.html",
                title="Sin proyecto",
                heading="Ese proyecto no existe",
                text="Créalo desde el flujo.",
                status_code=404,
            )
        referencias = []
        for reel_id in proyecto.referencias:
            entry = db.get_bank_entry(reel_id)
            if entry is not None:
                referencias.append(entry)
        return render(
            "flujo_detalle.html",
            nav="flujo",
            title=proyecto.titulo,
            proyecto=proyecto,
            pasos=PASOS,
            etiquetas=ETIQUETAS,
            campos=CAMPOS,
            etiquetas_perfil=PERFIL_ETIQUETAS,
            banco=[entry for entry in db.list_bank() if not entry.is_example],
            referencias=referencias,
            aviso=aviso,
            error=error,
        )

    def _flujo_error(proyecto_id: int, exc: FlujoError) -> HTMLResponse:
        response = flujo_detalle(proyecto_id, error=str(exc))
        response.status_code = 400
        return response

    @app.post("/flujo/{proyecto_id}/oferta")
    async def flujo_oferta(proyecto_id: int, request: Request) -> Response:
        form = await request.form()
        try:
            aplicar_oferta(db, proyecto_id, perfil_desde_formulario(form))
        except FlujoError as exc:
            return _flujo_error(proyecto_id, exc)
        return RedirectResponse(f"/flujo/{proyecto_id}?aviso=Oferta+guardada.", status_code=303)

    @app.post("/flujo/{proyecto_id}/referencias")
    async def flujo_referencias(proyecto_id: int, request: Request) -> Response:
        form = await request.form()
        try:
            ids = [int(value) for value in form.getlist("reel")]
            aplicar_referencias(db, proyecto_id, ids)
        except (FlujoError, ValueError) as exc:
            return _flujo_error(proyecto_id, FlujoError(str(exc)))
        return RedirectResponse(f"/flujo/{proyecto_id}?aviso=Referencias+guardadas.", status_code=303)

    @app.post("/flujo/{proyecto_id}/hook")
    async def flujo_hook(proyecto_id: int, request: Request) -> Response:
        form = await request.form()
        try:
            aplicar_hook(db, proyecto_id, int(str(form.get("reel") or "")))
        except (FlujoError, ValueError) as exc:
            return _flujo_error(proyecto_id, FlujoError(str(exc)))
        return RedirectResponse(f"/flujo/{proyecto_id}?aviso=Gancho+elegido.", status_code=303)

    @app.post("/flujo/{proyecto_id}/guion")
    def flujo_guion(proyecto_id: int) -> Response:
        try:
            aplicar_guion(db, proyecto_id)
        except FlujoError as exc:
            return _flujo_error(proyecto_id, exc)
        return RedirectResponse(f"/flujo/{proyecto_id}?aviso=Guion+generado.", status_code=303)

    @app.post("/flujo/{proyecto_id}/variantes")
    def flujo_variantes(proyecto_id: int) -> Response:
        try:
            aplicar_variantes(db, proyecto_id)
        except FlujoError as exc:
            return _flujo_error(proyecto_id, exc)
        return RedirectResponse(f"/flujo/{proyecto_id}?aviso=Variantes+generadas.", status_code=303)

    @app.post("/flujo/{proyecto_id}/revision")
    async def flujo_revision(proyecto_id: int, request: Request) -> Response:
        form = await request.form()
        try:
            aplicar_revision(
                db,
                proyecto_id,
                str(form.get("notas") or ""),
                aprobar=form.get("aprobar") == "1",
            )
        except FlujoError as exc:
            return _flujo_error(proyecto_id, exc)
        return RedirectResponse(f"/flujo/{proyecto_id}?aviso=Revisión+guardada.", status_code=303)

    @app.get("/flujo/{proyecto_id}/exportar")
    def flujo_exportar(proyecto_id: int) -> Response:
        try:
            texto = exportar_proyecto(db, proyecto_id)
        except FlujoError as exc:
            return _flujo_error(proyecto_id, exc)
        return PlainTextResponse(
            texto,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=proyecto-{proyecto_id}.md"},
        )

    @app.get("/tiktok", response_class=HTMLResponse)
    def tiktok(request: Request) -> HTMLResponse:
        from radar.tiktok.store import contar_por_estado, listar_cuentas, ultima_pasada, valores_distintos

        estado = (request.query_params.get("estado") or "califica").strip() or "califica"
        vistas = _vistas_param(request.query_params.get("vistas"))
        filtros = {
            "q": request.query_params.get("q") or "",
            "nicho": request.query_params.get("nicho") or "",
            "subnicho": request.query_params.get("subnicho") or "",
            "vistas": request.query_params.get("vistas") or "",
        }
        return render(
            "tiktok.html",
            nav="tiktok",
            title="Cuentas TikTok",
            cuentas=listar_cuentas(
                db,
                nicho=filtros["nicho"] or None,
                subnicho=filtros["subnicho"] or None,
                estado=estado,
                min_vistas=vistas,
                q=filtros["q"] or None,
            ),
            nichos=valores_distintos(db, "nicho", estado=estado),
            subnichos=valores_distintos(db, "subnicho", estado=estado),
            conteos=contar_por_estado(db),
            pasada=ultima_pasada(db),
            filtros=filtros,
            estado=estado,
            aviso=request.query_params.get("aviso") or "",
        )

    @app.get("/tiktok/exportar.csv")
    def tiktok_csv() -> PlainTextResponse:
        from radar.tiktok.csvio import exportar_csv

        return PlainTextResponse(
            exportar_csv(db, estado="califica"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=cuentas-tiktok.csv"},
        )

    @app.get("/tiktok/{handle}", response_class=HTMLResponse)
    def tiktok_detalle(handle: str) -> HTMLResponse:
        from radar.tiktok.store import obtener_cuenta

        cuenta = obtener_cuenta(db, handle)
        if cuenta is None:
            return render(
                "vacio.html",
                title="No está en el radar",
                heading="Esa cuenta no está en el radar de TikTok",
                text="Solo aparecen las que se han importado o leído.",
                status_code=404,
            )
        return render(
            "tiktok_detalle.html",
            nav="tiktok",
            title=f"@{cuenta['handle']}",
            cuenta=cuenta,
            piezas=_piezas_virales(cuenta.get("piezas_virales") or ""),
        )

    @app.post("/tiktok/semilla")
    async def tiktok_semilla(request: Request) -> Response:
        from radar.tiktok.store import anadir_semilla

        form = await request.form()
        valor = str(form.get("valor") or "").strip()
        tipo = str(form.get("tipo") or "").strip() or None
        try:
            tipo_resuelto, limpio = _tipo_semilla_web(valor, tipo)
            anadir_semilla(db, tipo_resuelto, limpio)
        except ValueError as exc:
            return render(
                "vacio.html",
                title="Semilla no válida",
                heading="No se ha guardado la semilla",
                text=str(exc),
                status_code=400,
            )
        return RedirectResponse("/tiktok?aviso=Semilla+guardada.", status_code=303)

    @app.get("/instagram", response_class=HTMLResponse)
    def instagram(request: Request) -> HTMLResponse:
        from radar.instagram.store import contar_por_estado, listar_cuentas, ultima_pasada, valores_distintos

        estado = (request.query_params.get("estado") or "califica").strip() or "califica"
        vistas = _vistas_param(request.query_params.get("vistas"))
        seguidores = _vistas_param(request.query_params.get("seguidores"))
        multiplo = _decimal_param(request.query_params.get("multiplo"))
        filtros = {
            "q": request.query_params.get("q") or "",
            "nicho": request.query_params.get("nicho") or "",
            "subnicho": request.query_params.get("subnicho") or "",
            "idioma": request.query_params.get("idioma") or "",
            "seguidores": request.query_params.get("seguidores") or "",
            "vistas": request.query_params.get("vistas") or "",
            "multiplo": request.query_params.get("multiplo") or "",
        }
        return render(
            "instagram.html",
            nav="instagram",
            title="Creadores de Instagram",
            cuentas=listar_cuentas(
                db,
                nicho=filtros["nicho"] or None,
                subnicho=filtros["subnicho"] or None,
                idioma=filtros["idioma"] or None,
                estado=estado,
                min_vistas=vistas,
                min_seguidores=seguidores,
                min_multiplo=multiplo,
                q=filtros["q"] or None,
            ),
            nichos=valores_distintos(db, "nicho", estado=estado),
            subnichos=valores_distintos(db, "subnicho", estado=estado),
            idiomas=valores_distintos(db, "idioma", estado=estado),
            conteos=contar_por_estado(db),
            pasada=ultima_pasada(db),
            filtros=filtros,
            estado=estado,
            aviso=request.query_params.get("aviso") or "",
        )

    @app.get("/instagram/exportar.csv")
    def instagram_csv() -> PlainTextResponse:
        from radar.instagram.csvio import exportar_csv

        return PlainTextResponse(
            exportar_csv(db, estado="califica"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=cuentas-instagram.csv"},
        )

    @app.get("/instagram/{handle}", response_class=HTMLResponse)
    def instagram_detalle(handle: str) -> HTMLResponse:
        from radar.instagram.store import obtener_cuenta

        cuenta = obtener_cuenta(db, handle)
        if cuenta is None:
            return render(
                "vacio.html",
                title="No está en el radar",
                heading="Ese creador no está en el radar de Instagram",
                text="Solo aparecen los que se han importado o leído.",
                status_code=404,
            )
        return render(
            "instagram_detalle.html",
            nav="instagram",
            title=f"@{cuenta['handle']}",
            cuenta=cuenta,
            piezas=_piezas_virales(cuenta.get("piezas_virales") or ""),
        )

    @app.post("/instagram/semilla")
    async def instagram_semilla(request: Request) -> Response:
        from radar.instagram.store import anadir_semilla

        form = await request.form()
        valor = str(form.get("valor") or "").strip()
        tipo = str(form.get("tipo") or "").strip() or None
        try:
            tipo_resuelto, limpio = _tipo_semilla_web(valor, tipo)
            anadir_semilla(db, tipo_resuelto, limpio)
        except ValueError as exc:
            return render(
                "vacio.html",
                title="Semilla no válida",
                heading="No se ha guardado la semilla",
                text=str(exc),
                status_code=400,
            )
        return RedirectResponse("/instagram?aviso=Semilla+guardada.", status_code=303)

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
            navegador_listo=settings.browser_ready,
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
    shown_source = (
        source_requested
        if source_requested in {"playwright", "apify", "instaloader", "auto"}
        else "playwright"
    )
    bloqueo = ""
    if plan is not None:
        bloqueo = block_reason(settings, plan, allow_over_budget=True) or ""
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
        bloqueo=bloqueo,
        puede_lanzar=puede_lanzar,
        running=running,
        runs=db.list_runs(),
        last_error=db.get_meta("last_error") or "",
        status_code=status_code,
    )


def _decimal_param(value) -> float | None:
    bruto = str(value or "").strip().replace(",", ".")
    if not bruto:
        return None
    try:
        numero = float(bruto)
    except ValueError:
        return None
    return numero if numero >= 0 else None


def _vistas_param(value) -> int | None:
    bruto = str(value or "").strip().replace(".", "").replace(",", "")
    if not bruto:
        return None
    try:
        numero = int(bruto)
    except ValueError:
        return None
    return numero if numero >= 0 else None


def _piezas_virales(texto: str) -> list[dict[str, str]]:
    piezas = []
    for trozo in (texto or "").split(" ; "):
        limpio = trozo.strip()
        if not limpio:
            continue
        url, _, resto = limpio.partition(" | ")
        piezas.append({"url": url.strip(), "resto": resto.strip()})
    return piezas


def _tipo_semilla_web(valor: str, tipo: str | None) -> tuple[str, str]:
    if tipo in {"keyword", "handle", "hashtag", "url"}:
        return tipo, valor
    if valor.lower().startswith(("http://", "https://")):
        return "url", valor
    if valor.startswith("#"):
        return "hashtag", valor[1:]
    if valor.startswith("@"):
        return "handle", valor[1:]
    return "keyword", valor


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
