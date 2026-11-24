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
from radar.sources.instaloader_source import (
    InstagramBlocked,
    InstagramNotConfigured,
    InstaloaderNotInstalled,
    login_secondary_account,
)
from radar.sources.select import plan_source
from radar.usernames import UsernameError, normalize_username


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Motor de ganchos y formatos. Estima el coste antes de llamar a Apify.",
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
        choices=("auto", "apify", "instaloader"),
        default=None,
        help="auto usa Apify si hay token y el coste cabe; si no, Instaloader",
    )

    login = sub.add_parser("ig-login", help="Inicia sesión en la cuenta secundaria y guarda la sesión")
    login.add_argument("--username", default=None, help="Cuenta secundaria. Por defecto IG_USERNAME")

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
        if args.command == "ig-login":
            return _ig_login(settings, args.username)
        if args.command == "export":
            return _export(db, args.fmt, args.out)
        if args.command == "import":
            return _import(db, args.path)
        if args.command == "seed":
            seed_examples(db)
            print("Watchlist y reels de ejemplo cargados. Las cifras no son reales.")
            return 0
        if args.command == "accounts":
            return _accounts(db, args)
    except (
        MissingApifyToken,
        OverBudget,
        NoActiveAccounts,
        ApifyError,
        InstagramBlocked,
        InstagramNotConfigured,
        InstaloaderNotInstalled,
        UsernameError,
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
    )
    print(outcome.message)
    return 0 if outcome.status == "succeeded" else 2


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


def _serve(settings, host: str | None, port: int | None) -> int:
    import uvicorn

    from radar.web import create_app

    app = create_app(settings)
    uvicorn.run(app, host=host or settings.host, port=port or settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
