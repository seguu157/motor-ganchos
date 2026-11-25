"""Interfaz de línea de comandos. El cron semanal usa `radar run --yes`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from radar.apify_client import ApifyError, MissingApifyToken
from radar.config import load_settings
from radar.db import Database
from radar.export_json import dumps_document, export_document, import_document
from radar.export_md import render_markdown
from radar.pipeline import (
    NoActiveAccounts,
    OverBudget,
    describe_plan,
    execute_selected_run,
)
from radar.seed import seed_examples, seed_if_needed
from radar.flujo import FlujoError
from radar.perfil import CAMPOS, perfil_desde_dict
from radar.sources.instaloader_source import (
    InstagramBlocked,
    InstagramNotConfigured,
    InstaloaderNotInstalled,
    login_secondary_account,
)
from radar.instagram.fuente import InstagramBloqueado, InstagramSesionAusente
from radar.sources.playwright_source import (
    BrowserBlocked,
    BrowserNotConfigured,
    PlaywrightNotInstalled,
    login_browser,
)
from radar.sources.select import plan_source
from radar.usernames import UsernameError, normalize_username


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Motor de ganchos y formatos. La fuente por defecto es el navegador.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Estima y, si confirmas, ingiere reels")
    run.add_argument("--yes", action="store_true", help="Lanza la ingesta sin preguntar")
    run.add_argument("--dry-run", action="store_true", help="Solo muestra la estimación")
    run.add_argument("--skip-transcribe", action="store_true")
    run.add_argument("--skip-llm", action="store_true")
    run.add_argument("--reels", type=int, default=None, help="Reels por cuenta. Por defecto 20")
    run.add_argument("--allow-over-budget", action="store_true")
    run.add_argument(
        "--source",
        choices=("playwright", "apify", "instaloader", "auto"),
        default=None,
        help="playwright es la fuente por defecto; apify e instaloader se piden aparte",
    )
    run.add_argument(
        "--conservar-imagenes",
        action="store_true",
        help="No borra las imágenes de carrusel después del OCR",
    )

    login = sub.add_parser(
        "login",
        help="Abre Chromium para entrar a mano en la cuenta secundaria. No pide la contraseña",
    )
    login.add_argument("--username", default=None, help="Solo una etiqueta. No es la contraseña")

    ig_login = sub.add_parser("ig-login", help="Sesión de Instaloader, el respaldo. No guarda la contraseña")
    ig_login.add_argument("--username", default=None, help="Cuenta secundaria. Por defecto IG_USERNAME")

    serve = sub.add_parser("serve", help="Abre la interfaz web")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    export = sub.add_parser("export", help="Exporta el banco a Markdown o una copia JSON")
    export.add_argument("fmt", choices=("md", "json"))
    export.add_argument("--out", default="-", help="Ruta de salida, o - para la terminal")

    import_cmd = sub.add_parser("import", help="Restaura una copia JSON")
    import_cmd.add_argument("fmt", choices=("json",))
    import_cmd.add_argument("path")

    sub.add_parser("seed", help="Carga la watchlist y los reels de ejemplo")
    sub.add_parser("llm-check", help="Comprueba el backend de lenguaje con un prompt mínimo")

    perfil = sub.add_parser("perfil", help="Oferta, audiencia y CTA de Antonio")
    perfil_sub = perfil.add_subparsers(dest="perfil_command", required=True)
    perfil_sub.add_parser("ver")
    guardar = perfil_sub.add_parser("guardar")
    for campo in CAMPOS:
        guardar.add_argument(f"--{campo}", default=None)

    flujo = sub.add_parser("flujo", help="Oferta, referencias, hooks, guion, variantes, revisión y exportación")
    flujo_sub = flujo.add_subparsers(dest="flujo_command", required=True)
    flujo_sub.add_parser("list")
    nuevo = flujo_sub.add_parser("nuevo")
    nuevo.add_argument("titulo")
    ver = flujo_sub.add_parser("ver")
    ver.add_argument("id", type=int)
    oferta = flujo_sub.add_parser("oferta")
    oferta.add_argument("id", type=int)
    for campo in CAMPOS:
        oferta.add_argument(f"--{campo}", default=None)
    referencias = flujo_sub.add_parser("referencias")
    referencias.add_argument("id", type=int)
    referencias.add_argument("--reel", type=int, action="append", required=True)
    hook = flujo_sub.add_parser("hook")
    hook.add_argument("id", type=int)
    hook.add_argument("--reel", type=int, required=True)
    guion = flujo_sub.add_parser("guion")
    guion.add_argument("id", type=int)
    variantes = flujo_sub.add_parser("variantes")
    variantes.add_argument("id", type=int)
    revision = flujo_sub.add_parser("revision")
    revision.add_argument("id", type=int)
    revision.add_argument("--notas", default="")
    revision.add_argument("--aprobar", action="store_true")
    exportar_flujo = flujo_sub.add_parser("exportar")
    exportar_flujo.add_argument("id", type=int)
    exportar_flujo.add_argument("--out", default="-")

    banco = sub.add_parser("banco", help="Lista la biblioteca con filtros")
    banco.add_argument("--cuenta", default=None)
    banco.add_argument("--tipo", default=None)
    banco.add_argument("--categoria", default=None)
    banco.add_argument("--formato", choices=("reel", "carrusel"), default=None)
    banco.add_argument("--q", default=None)
    banco.add_argument("--ocultar-ejemplos", action="store_true")

    cuentas = sub.add_parser("cuentas", help="Radar de cuentas TikTok que venden con slideshows")
    cuentas_sub = cuentas.add_subparsers(dest="cuentas_command", required=True)
    descubrir = cuentas_sub.add_parser("descubrir", help="Busca cuentas nuevas y las lee")
    descubrir.add_argument("--yes", action="store_true", help="Lee TikTok. Sin esto solo enseña el plan")
    descubrir.add_argument("--max", type=int, default=None, help="Cuentas a leer en esta pasada")
    descubrir.add_argument("--paginas", type=int, default=None, help="Páginas públicas (listas, hashtags) a abrir")
    descubrir.add_argument("--sin-llm", action="store_true", help="Clasifica solo con la heurística")
    descubrir.add_argument("--semilla", action="append", default=[], help="keyword, @handle, #hashtag o https://...")
    descubrir.add_argument("--pausa", default=None, help="Pausa al azar, mínimo-máximo en segundos")
    descubrir.add_argument("--apify", action="store_true", help="Opcional. Sin token no llama a Apify")
    actualizar = cuentas_sub.add_parser("actualizar", help="Vuelve a leer cuentas que ya califican")
    actualizar.add_argument("--yes", action="store_true")
    actualizar.add_argument("--max", type=int, default=None)
    actualizar.add_argument("--handle", default=None)
    actualizar.add_argument("--sin-llm", action="store_true")
    actualizar.add_argument("--pausa", default=None)
    ciclo = cuentas_sub.add_parser("ciclo", help="Descubre y, si TikTok no bloquea, actualiza")
    ciclo.add_argument("--yes", action="store_true")
    ciclo.add_argument("--max", type=int, default=None)
    ciclo.add_argument("--sin-llm", action="store_true")
    ciclo.add_argument("--pausa", default=None)
    ciclo.add_argument("--apify", action="store_true")
    exportar = cuentas_sub.add_parser("exportar", help="CSV con las columnas del corte")
    exportar.add_argument("--out", default="-")
    exportar.add_argument("--todas", action="store_true", help="Incluye candidatas y descartadas")
    importar = cuentas_sub.add_parser("importar", help="Importa un CSV. Sin ruta, carga el corte incluido")
    importar.add_argument("path", nargs="?", default=None)
    listar = cuentas_sub.add_parser("listar", help="Lista las cuentas guardadas")
    listar.add_argument("--nicho", default=None)
    listar.add_argument("--subnicho", default=None)
    listar.add_argument("--min-vistas", type=int, default=None)
    listar.add_argument("--estado", default="califica")
    listar.add_argument("--q", default=None)
    programar = cuentas_sub.add_parser("programar", help="Comando del Programador de tareas de Windows")
    programar.add_argument("--hora", default="07:00")
    programar.add_argument("--tarea", default="RadarCuentasTikTok")
    programar.add_argument("--instalar", action="store_true", help="En Windows, registra la tarea")
    semilla = cuentas_sub.add_parser("semilla", help="Añade una semilla de descubrimiento")
    semilla.add_argument("valor")
    semilla.add_argument("--tipo", default=None, choices=("keyword", "handle", "hashtag", "url"))
    cuentas_sub.add_parser("semillas", help="Lista las semillas activas")

    creadores = sub.add_parser("creadores", help="Radar de creadores de Instagram del nicho de IA")
    creadores_sub = creadores.add_subparsers(dest="creadores_command", required=True)
    ig_descubrir = creadores_sub.add_parser("descubrir", help="Busca creadores nuevos y lee su pestaña Reels")
    ig_descubrir.add_argument("--yes", action="store_true", help="Abre Instagram. Sin esto solo enseña el plan")
    ig_descubrir.add_argument("--max", type=int, default=None, help="Perfiles a leer en esta pasada")
    ig_descubrir.add_argument("--paginas", type=int, default=None, help="Páginas de hashtag o listas a abrir")
    ig_descubrir.add_argument("--sin-llm", action="store_true", help="Clasifica solo con la heurística")
    ig_descubrir.add_argument("--semilla", action="append", default=[], help="keyword, @usuario, #hashtag o https://...")
    ig_descubrir.add_argument("--pausa", default=None, help="Pausa al azar entre perfiles, mínimo-máximo en segundos")
    ig_descubrir.add_argument("--apify", action="store_true", help="Opcional. Sin token ni actor no llama a Apify")
    ig_actualizar = creadores_sub.add_parser("actualizar", help="Vuelve a leer creadores que ya califican")
    ig_actualizar.add_argument("--yes", action="store_true")
    ig_actualizar.add_argument("--max", type=int, default=None)
    ig_actualizar.add_argument("--handle", default=None)
    ig_actualizar.add_argument("--sin-llm", action="store_true")
    ig_actualizar.add_argument("--pausa", default=None)
    ig_ciclo = creadores_sub.add_parser("ciclo", help="Descubre y, si Instagram no bloquea, actualiza")
    ig_ciclo.add_argument("--yes", action="store_true")
    ig_ciclo.add_argument("--max", type=int, default=None)
    ig_ciclo.add_argument("--sin-llm", action="store_true")
    ig_ciclo.add_argument("--pausa", default=None)
    ig_ciclo.add_argument("--apify", action="store_true")
    ig_exportar = creadores_sub.add_parser("exportar", help="CSV con las columnas del corte")
    ig_exportar.add_argument("--out", default="-")
    ig_exportar.add_argument("--todas", action="store_true", help="Incluye candidatas y descartadas")
    ig_importar = creadores_sub.add_parser("importar", help="Importa un CSV. Sin ruta, carga el corte incluido")
    ig_importar.add_argument("path", nargs="?", default=None)
    ig_listar = creadores_sub.add_parser("listar", help="Lista los creadores guardados")
    ig_listar.add_argument("--nicho", default=None)
    ig_listar.add_argument("--subnicho", default=None)
    ig_listar.add_argument("--idioma", default=None)
    ig_listar.add_argument("--min-vistas", type=int, default=None)
    ig_listar.add_argument("--min-seguidores", type=int, default=None)
    ig_listar.add_argument("--min-multiplo", type=float, default=None)
    ig_listar.add_argument("--estado", default="califica")
    ig_listar.add_argument("--q", default=None)
    ig_programar = creadores_sub.add_parser("programar", help="Comando del Programador de tareas de Windows")
    ig_programar.add_argument("--hora", default="07:30")
    ig_programar.add_argument("--tarea", default="RadarCreadoresInstagram")
    ig_programar.add_argument("--instalar", action="store_true", help="En Windows, registra la tarea")
    ig_semilla = creadores_sub.add_parser("semilla", help="Añade una semilla de descubrimiento")
    ig_semilla.add_argument("valor")
    ig_semilla.add_argument("--tipo", default=None, choices=("keyword", "handle", "hashtag", "url"))
    creadores_sub.add_parser("semillas", help="Lista las semillas activas")

    accounts = sub.add_parser("accounts", help="Watchlist de cuentas")
    account_sub = accounts.add_subparsers(dest="account_command", required=True)
    account_sub.add_parser("list")
    add = account_sub.add_parser("add")
    add.add_argument("username")
    remove = account_sub.add_parser("remove")
    remove.add_argument("username")

    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.command == "serve":
        return _serve(settings, args.host, args.port)

    db = Database(settings.db_path)
    seed_if_needed(db)
    try:
        if args.command == "run":
            return _run(db, settings, args)
        if args.command == "login":
            return _browser_login(settings, args.username)
        if args.command == "ig-login":
            return _ig_login(settings, args.username)
        if args.command == "perfil":
            return _perfil(db, args)
        if args.command == "flujo":
            return _flujo(db, args)
        if args.command == "banco":
            return _banco(db, args)
        if args.command == "export":
            return _export(db, args.fmt, args.out)
        if args.command == "import":
            return _import(db, args.path)
        if args.command == "seed":
            seed_examples(db)
            print("Watchlist y reels de ejemplo cargados. Las cifras no son reales.")
            return 0
        if args.command == "llm-check":
            return _llm_check(settings)
        if args.command == "accounts":
            return _accounts(db, args)
        if args.command == "cuentas":
            return _cuentas(db, settings, args)
        if args.command == "creadores":
            return _creadores(db, settings, args)
    except (
        MissingApifyToken,
        OverBudget,
        NoActiveAccounts,
        ApifyError,
        InstagramBlocked,
        InstagramNotConfigured,
        InstaloaderNotInstalled,
        BrowserBlocked,
        BrowserNotConfigured,
        PlaywrightNotInstalled,
        FlujoError,
        UsernameError,
        InstagramSesionAusente,
        InstagramBloqueado,
        ValueError,
        OSError,
    ) as exc:
        print(exc, file=sys.stderr)
        return 2
    return 2


def _run(db: Database, settings, args) -> int:
    usernames = db.list_active_usernames()
    if not usernames:
        raise NoActiveAccounts("No hay cuentas activas en la watchlist.")
    limit = args.reels or settings.reels_per_account
    plan = plan_source(
        settings,
        usernames,
        limit,
        requested=args.source,
        allow_over_budget=args.allow_over_budget,
    )
    print(describe_plan(plan))
    if args.dry_run:
        print("Dry-run: no se ha hecho ninguna petición.")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print("Añade --yes para lanzarla. No se ha hecho ninguna petición.", file=sys.stderr)
            return 2
        answer = input("¿Lanzar la ingesta de metadatos? [s/N] ").strip().lower()
        if answer not in {"s", "si", "sí", "y", "yes"}:
            print("Cancelado. No se ha hecho ninguna petición.")
            return 0
    outcome = execute_selected_run(
        db,
        settings,
        reels_per_account=args.reels,
        allow_over_budget=args.allow_over_budget,
        yes=True,
        skip_transcribe=args.skip_transcribe,
        skip_llm=args.skip_llm,
        requested_source=args.source,
        conservar_imagenes=args.conservar_imagenes,
    )
    print(outcome.message)
    return 0 if outcome.status == "succeeded" else 2


def _llm_check(settings) -> int:
    from radar.llm import LLMError
    from radar.llm_codex import check_backend

    try:
        print(check_backend(settings))
    except LLMError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


def _browser_login(settings, username_arg: str | None) -> int:
    username = (username_arg or "").strip().lstrip("@")
    login_browser(settings.browser_profile, username=username or None)
    print(
        f"Sesión de navegador lista en {settings.browser_profile}. "
        "La contraseña no se guarda. El marcador solo anota la etiqueta, si la pasaste."
    )
    return 0


def _ig_login(settings, username_arg: str | None) -> int:
    username = (username_arg or settings.ig_username or "").strip().lstrip("@")
    if not username:
        if not sys.stdin.isatty():
            print("Pasa --username o define IG_USERNAME.", file=sys.stderr)
            return 2
        username = input("Usuario de la cuenta secundaria de Instagram: ").strip().lstrip("@")
    if not username:
        print("Falta el usuario de la cuenta secundaria.", file=sys.stderr)
        return 2
    login_secondary_account(username, settings.ig_session_file)
    print(f"Sesión guardada en {settings.ig_session_file} para @{username}. La contraseña no se guarda.")
    return 0


def _export(db: Database, fmt: str, out: str) -> int:
    if fmt == "md":
        text = render_markdown(db.list_bank())
    else:
        text = dumps_document(export_document(db))
    if out == "-":
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        Path(out).write_text(text, encoding="utf-8")
        print(f"Escrito {out}")
    return 0


def _import(db: Database, path: str) -> int:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    saved = import_document(db, document)
    print(f"Importados {saved} reels.")
    return 0


def _perfil(db: Database, args) -> int:
    if args.perfil_command == "ver":
        perfil = db.get_perfil()
        for campo in CAMPOS:
            print(f"{campo}: {perfil.hueco(campo)}")
        return 0
    actual = db.get_perfil().as_dict()
    for campo in CAMPOS:
        valor = getattr(args, campo)
        if valor is not None:
            actual[campo] = valor
    db.save_perfil(perfil_desde_dict(actual))
    print("Perfil guardado. Lo que dejes vacío se sigue viendo como [X].")
    return 0


def _flujo(db: Database, args) -> int:
    from radar.flujo import (
        aplicar_guion,
        aplicar_hook,
        aplicar_oferta,
        aplicar_referencias,
        aplicar_revision,
        aplicar_variantes,
        crear_proyecto,
        exportar_proyecto,
        listar_proyectos,
        cargar_proyecto,
    )

    if args.flujo_command == "list":
        proyectos = listar_proyectos(db)
        if not proyectos:
            print("No hay proyectos.")
            return 0
        for proyecto in proyectos:
            print(f"{proyecto.id}  {proyecto.estado}  {proyecto.paso}  {proyecto.titulo}")
        return 0
    if args.flujo_command == "nuevo":
        proyecto = crear_proyecto(db, args.titulo)
        print(f"Proyecto {proyecto.id}: {proyecto.titulo}. Paso: oferta.")
        return 0
    if args.flujo_command == "ver":
        proyecto = cargar_proyecto(db, args.id)
        if proyecto is None:
            raise FlujoError(f"No existe el proyecto {args.id}.")
        print(f"{proyecto.id}  {proyecto.titulo}")
        print(f"Estado: {proyecto.estado}. Paso: {proyecto.paso}.")
        print(f"Referencias: {proyecto.referencias or 'ninguna'}")
        print(f"Gancho: {proyecto.hook_reel_id or 'ninguno'}")
        if proyecto.guion:
            print(proyecto.guion)
        return 0
    if args.flujo_command == "oferta":
        actual = db.get_perfil().as_dict()
        for campo in CAMPOS:
            valor = getattr(args, campo)
            if valor is not None:
                actual[campo] = valor
        aplicar_oferta(db, args.id, perfil_desde_dict(actual))
        print("Oferta guardada. Siguiente paso: referencias.")
        return 0
    if args.flujo_command == "referencias":
        aplicar_referencias(db, args.id, list(args.reel))
        print("Referencias guardadas. Siguiente paso: hook.")
        return 0
    if args.flujo_command == "hook":
        aplicar_hook(db, args.id, args.reel)
        print("Gancho elegido. Siguiente paso: guion.")
        return 0
    if args.flujo_command == "guion":
        proyecto = aplicar_guion(db, args.id)
        print(proyecto.guion)
        return 0
    if args.flujo_command == "variantes":
        proyecto = aplicar_variantes(db, args.id)
        for variante in proyecto.variantes:
            print(f"--- {variante['id']} ---")
            print(variante["texto"])
        return 0
    if args.flujo_command == "revision":
        proyecto = aplicar_revision(db, args.id, args.notas, aprobar=args.aprobar)
        print(f"Estado: {proyecto.estado}. Siguiente paso: exportar.")
        return 0
    texto = exportar_proyecto(db, args.id)
    if args.out == "-":
        sys.stdout.write(texto)
    else:
        Path(args.out).write_text(texto, encoding="utf-8")
        print(f"Escrito {args.out}")
    return 0


def _banco(db: Database, args) -> int:
    from radar.export_json import filter_entries

    entries = filter_entries(
        db.list_bank(),
        account=args.cuenta,
        hook_type=args.tipo,
        category=args.categoria,
        media_kind=args.formato,
        query=args.q,
        hide_examples=args.ocultar_ejemplos,
    )
    if not entries:
        print("Ningún gancho con esos filtros.")
        return 0
    for entry in entries:
        print(
            f"{entry.reel_id}  {entry.categoria}  {entry.media_kind}  "
            f"@{entry.account}  {entry.hook or 'pendiente'}"
        )
    return 0


def _accounts(db: Database, args) -> int:
    if args.account_command == "list":
        rows = db.list_accounts()
        if not rows:
            print("La watchlist está vacía.")
            return 0
        for row in rows:
            state = "activa" if row["active"] else "pausada"
            print(f"@{row['username']}  {state}")
        return 0
    username = normalize_username(args.username)
    if args.account_command == "add":
        db.upsert_account(username, active=True)
        print(f"Añadida @{username}")
        return 0
    db.delete_account(username)
    print(f"Eliminada @{username}")
    return 0


def _cuentas(db: Database, settings, args) -> int:
    from radar.tiktok.csvio import SEMILLA_CSV, exportar_csv, importar_csv, importar_semilla_si_falta
    from radar.tiktok.descubrir import ejecutar_actualizacion, ejecutar_ciclo, ejecutar_descubrimiento
    from radar.tiktok.programar import instalar_tarea, preparar_tarea
    from radar.tiktok.store import anadir_semilla, asegurar_semillas_defecto, listar_cuentas, listar_semillas

    if args.cuentas_command == "importar":
        ruta = args.path or SEMILLA_CSV
        total = importar_csv(db, ruta)
        print(f"Importadas {total} cuentas desde {ruta}.")
        return 0
    importar_semilla_si_falta(db)
    if args.cuentas_command == "exportar":
        estado = "todas" if args.todas else "califica"
        texto = exportar_csv(db, None if args.out == "-" else args.out, estado=estado)
        if args.out == "-":
            sys.stdout.write(texto)
        else:
            print(f"Escrito {args.out}")
        return 0
    if args.cuentas_command == "listar":
        cuentas = listar_cuentas(
            db,
            nicho=args.nicho,
            subnicho=args.subnicho,
            estado=args.estado,
            min_vistas=args.min_vistas,
            q=args.q,
        )
        if not cuentas:
            print("Ninguna cuenta con esos filtros.")
            return 0
        for cuenta in cuentas:
            vistas = cuenta.get("mejor_pieza_vistas")
            vistas_txt = "—" if vistas is None else str(vistas)
            print(
                f"@{cuenta['handle']}  {cuenta.get('nicho') or '—'}  "
                f"{cuenta.get('subnicho') or '—'}  {vistas_txt}  {cuenta.get('que_vende') or ''}"
            )
        return 0
    if args.cuentas_command == "semillas":
        asegurar_semillas_defecto(db)
        for semilla in listar_semillas(db):
            print(f"{semilla['tipo']}  {semilla['valor']}")
        return 0
    if args.cuentas_command == "semilla":
        tipo, valor = _tipo_semilla(args.valor, args.tipo)
        anadir_semilla(db, tipo, valor)
        print(f"Semilla {tipo}: {valor}")
        return 0
    if args.cuentas_command == "programar":
        programa = preparar_tarea(hora=args.hora, tarea=args.tarea)
        print(programa.script)
        print(programa.comando)
        if args.instalar:
            print(instalar_tarea(programa))
        return 0
    for texto in getattr(args, "semilla", []) or []:
        tipo, valor = _tipo_semilla(texto, None)
        anadir_semilla(db, tipo, valor)
    pausa = _pausa(args.pausa) if getattr(args, "pausa", None) else None
    if args.cuentas_command == "descubrir":
        informe = ejecutar_descubrimiento(
            db,
            settings,
            permitir_red=args.yes,
            max_cuentas=args.max,
            max_paginas=args.paginas,
            sin_llm=args.sin_llm,
            usar_apify=args.apify,
            pausa=pausa,
        )
    elif args.cuentas_command == "actualizar":
        informe = ejecutar_actualizacion(
            db,
            settings,
            permitir_red=args.yes,
            max_cuentas=args.max,
            handle=args.handle,
            sin_llm=args.sin_llm,
            pausa=pausa,
        )
    else:
        informe = ejecutar_ciclo(
            db,
            settings,
            permitir_red=args.yes,
            max_cuentas=args.max,
            sin_llm=args.sin_llm,
            usar_apify=args.apify,
            pausa=pausa,
        )
    print(informe.mensaje)
    if informe.status == "error":
        return 2
    return 0


def _creadores(db: Database, settings, args) -> int:
    from radar.instagram.csvio import SEMILLA_CSV, exportar_csv, importar_csv, importar_semilla_si_falta
    from radar.instagram.descubrir import ejecutar_actualizacion, ejecutar_ciclo, ejecutar_descubrimiento
    from radar.instagram.programar import instalar_tarea, preparar_tarea
    from radar.instagram.store import anadir_semilla, asegurar_semillas_defecto, listar_cuentas, listar_semillas

    if args.creadores_command == "importar":
        ruta = args.path or SEMILLA_CSV
        total = importar_csv(db, ruta)
        print(f"Importados {total} creadores desde {ruta}.")
        return 0
    importar_semilla_si_falta(db)
    if args.creadores_command == "exportar":
        estado = "todas" if args.todas else "califica"
        texto = exportar_csv(db, None if args.out == "-" else args.out, estado=estado)
        if args.out == "-":
            sys.stdout.write(texto)
        else:
            print(f"Escrito {args.out}")
        return 0
    if args.creadores_command == "listar":
        cuentas = listar_cuentas(
            db,
            nicho=args.nicho,
            subnicho=args.subnicho,
            idioma=args.idioma,
            estado=args.estado,
            min_vistas=args.min_vistas,
            min_seguidores=args.min_seguidores,
            min_multiplo=args.min_multiplo,
            q=args.q,
        )
        if not cuentas:
            print("Ningún creador con esos filtros.")
            return 0
        for cuenta in cuentas:
            vistas = cuenta.get("mejor_pieza_vistas")
            vistas_txt = "—" if vistas is None else str(vistas)
            multiplo = cuenta.get("mejor_pieza_multiplo")
            multiplo_txt = "—" if multiplo is None else f"{float(multiplo):.1f}×"
            seguidores = cuenta.get("seguidores")
            seguidores_txt = "—" if seguidores is None else str(seguidores)
            print(
                f"@{cuenta['handle']}  {cuenta.get('subnicho') or '—'}  "
                f"{cuenta.get('idioma') or '—'}  {seguidores_txt}  {vistas_txt}  {multiplo_txt}"
            )
        return 0
    if args.creadores_command == "semillas":
        asegurar_semillas_defecto(db)
        for semilla in listar_semillas(db):
            print(f"{semilla['tipo']}  {semilla['valor']}")
        return 0
    if args.creadores_command == "semilla":
        tipo, valor = _tipo_semilla(args.valor, args.tipo)
        anadir_semilla(db, tipo, valor)
        print(f"Semilla {tipo}: {valor}")
        return 0
    if args.creadores_command == "programar":
        programa = preparar_tarea(hora=args.hora, tarea=args.tarea)
        print(programa.script)
        print(programa.comando)
        if args.instalar:
            print(instalar_tarea(programa))
        return 0
    for texto in getattr(args, "semilla", []) or []:
        tipo, valor = _tipo_semilla(texto, None)
        anadir_semilla(db, tipo, valor)
    pausa = _pausa(args.pausa) if getattr(args, "pausa", None) else None
    if args.creadores_command == "descubrir":
        informe = ejecutar_descubrimiento(
            db,
            settings,
            permitir_red=args.yes,
            max_cuentas=args.max,
            max_paginas=args.paginas,
            sin_llm=args.sin_llm,
            usar_apify=args.apify,
            pausa=pausa,
        )
    elif args.creadores_command == "actualizar":
        informe = ejecutar_actualizacion(
            db,
            settings,
            permitir_red=args.yes,
            max_cuentas=args.max,
            handle=args.handle,
            sin_llm=args.sin_llm,
            pausa=pausa,
        )
    else:
        informe = ejecutar_ciclo(
            db,
            settings,
            permitir_red=args.yes,
            max_cuentas=args.max,
            sin_llm=args.sin_llm,
            usar_apify=args.apify,
            pausa=pausa,
        )
    print(informe.mensaje)
    if informe.status == "error":
        return 2
    return 0


def _tipo_semilla(valor: str, tipo: str | None) -> tuple[str, str]:
    if tipo:
        return tipo, valor
    bruto = (valor or "").strip()
    if bruto.lower().startswith(("http://", "https://")):
        return "url", bruto
    if bruto.startswith("#"):
        return "hashtag", bruto[1:]
    if bruto.startswith("@"):
        return "handle", bruto[1:]
    return "keyword", bruto


def _pausa(texto: str) -> tuple[float, float]:
    partes = texto.split("-")
    if len(partes) != 2:
        raise ValueError("La pausa tiene que ser mínimo-máximo, por ejemplo 2-6.")
    bajo = float(partes[0].strip().replace(",", "."))
    alto = float(partes[1].strip().replace(",", "."))
    if bajo < 0 or alto < bajo:
        raise ValueError("La pausa tiene que ir de menor a mayor.")
    return (bajo, alto)


def _serve(settings, host: str | None, port: int | None) -> int:
    import uvicorn

    from radar.web import create_app

    app = create_app(settings)
    uvicorn.run(app, host=host or settings.host, port=port or settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
