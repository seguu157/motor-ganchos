"""Watchlist inicial y reels de ejemplo.

Las cifras, los pies y las transcripciones son ficticios. Sirven para arrancar
la interfaz y los tests sin llamar a Apify ni atribuir textos reales a las cuentas.
"""

from __future__ import annotations

import json
from pathlib import Path

from radar.apify_parse import parse_payload
from radar.db import Database

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLE_ITEMS = FIXTURES / "apify_items.example.json"

WATCHLIST = (
    "jordigpt",
    "dani_llamazares",
    "victorinpublic",
    "crawlingmondays",
    "gregisenberg",
)

# Textos originales de demostración. No son transcripciones de esos creadores.
EXAMPLE_CONTENT: dict[str, dict] = {
    "EJEMPLOJG4": {
        "hook_type": "pregunta",
        "hook": "¿Sigues abriendo cinco pestañas para un solo informe?",
        "transcript": (
            "¿Sigues abriendo cinco pestañas para un solo informe? "
            "Yo lo hacía cada lunes con el cierre de la semana. "
            "El problema es que los datos viven en sitios distintos y se te va la mañana. "
            "El giro fue dejar un único flujo que reúne el cierre. "
            "Si te pasa, prueba a juntar las fuentes antes de redactar."
        ),
        "blocks": [
            {
                "id": "gancho",
                "texto": "¿Sigues [acción manual que tu audiencia reconoce]?",
                "en_pantalla": "Primer plano, hablando a cámara.",
            },
            {
                "id": "contexto",
                "texto": "Yo lo hacía [X] con [tarea recurrente].",
                "en_pantalla": "[X]",
            },
            {
                "id": "conflicto",
                "texto": "El problema es que [X] y se te va el tiempo.",
                "en_pantalla": "Gesto de saturación, sin capturas inventadas.",
            },
            {
                "id": "giro",
                "texto": "El giro fue [X].",
                "en_pantalla": "[X]",
            },
            {
                "id": "cierre",
                "texto": "Si te pasa, prueba [X].",
                "en_pantalla": "Cara a cámara, una sola frase.",
            },
        ],
        "template": (
            "Gancho: ¿Sigues [acción manual que tu audiencia reconoce]?\n"
            "Contexto: Yo lo hacía [X] con [tarea recurrente].\n"
            "Conflicto: El problema es que [X] y se te va el tiempo.\n"
            "Giro: El giro fue [X].\n"
            "Cierre: Si te pasa, prueba [X]."
        ),
        "adapted_script": (
            "¿Sigues [X]? Yo lo hacía [X] con el negocio de una persona. "
            "El problema es que [X] y se te va la mañana. "
            "El giro fue dejar que un flujo con IA reúna el cierre. "
            "Si te pasa, prueba [X] antes de grabar el siguiente reel."
        ),
    },
    "EJEMPLODL5": {
        "hook_type": "lista",
        "hook": "Tres cosas que quité del negocio antes de automatizar nada.",
        "transcript": (
            "Tres cosas que quité del negocio antes de automatizar nada. "
            "La primera era responder lo mismo cada día. "
            "La segunda, apuntar gastos en sitios distintos. "
            "La tercera, rehacer el calendario a mano. "
            "Cuando sobra ruido, la herramienta no arregla nada."
        ),
        "blocks": [
            {"id": "gancho", "texto": "[Número en palabras] cosas que quité antes de [X].", "en_pantalla": "Dedos contando, a cámara."},
            {"id": "contexto", "texto": "La primera era [X].", "en_pantalla": "[X]"},
            {"id": "conflicto", "texto": "La segunda, [X]. La tercera, [X].", "en_pantalla": "[X]"},
            {"id": "giro", "texto": "Cuando sobra ruido, la herramienta no arregla nada.", "en_pantalla": "Pausa, plano fijo."},
            {"id": "cierre", "texto": "Quita [X] antes de abrir otra app.", "en_pantalla": "Cierre a cámara."},
        ],
        "template": (
            "Gancho: [Número en palabras] cosas que quité antes de [X].\n"
            "Contexto: La primera era [X].\n"
            "Conflicto: La segunda, [X]. La tercera, [X].\n"
            "Giro: Cuando sobra ruido, la herramienta no arregla nada.\n"
            "Cierre: Quita [X] antes de abrir otra app."
        ),
        "adapted_script": (
            "Tres cosas que quité del negocio antes de automatizar nada. "
            "La primera era [X]. La segunda, [X]. La tercera, [X]. "
            "Cuando sobra ruido, la herramienta no arregla nada."
        ),
    },
    "EJEMPLOCM3": {
        "hook_type": "afirmación contraintuitiva",
        "hook": "Automatizar más no es el primer paso.",
        "transcript": (
            "Automatizar más no es el primer paso. "
            "Primero tienes que ver qué repites cada semana. "
            "Si no lo puedes decir en voz alta, no lo pongas en un flujo. "
            "El orden es observar, quitar y solo después automatizar."
        ),
        "blocks": [
            {"id": "gancho", "texto": "[Afirmación que contradice el impulso de la audiencia].", "en_pantalla": "A cámara, sin texto en pantalla todavía."},
            {"id": "contexto", "texto": "Primero tienes que ver qué repites [X].", "en_pantalla": "[X]"},
            {"id": "conflicto", "texto": "Si no lo puedes decir en voz alta, no lo pongas en un flujo.", "en_pantalla": "Plano corto."},
            {"id": "giro", "texto": "El orden es observar, quitar y solo después automatizar.", "en_pantalla": "Tres palabras, una cada vez."},
            {"id": "cierre", "texto": "Esta semana observa [X] y no abras otra herramienta.", "en_pantalla": "Cierre a cámara."},
        ],
        "template": (
            "Gancho: [Afirmación que contradice el impulso de la audiencia].\n"
            "Contexto: Primero tienes que ver qué repites [X].\n"
            "Conflicto: Si no lo puedes decir en voz alta, no lo pongas en un flujo.\n"
            "Giro: El orden es observar, quitar y solo después automatizar.\n"
            "Cierre: Esta semana observa [X] y no abras otra herramienta."
        ),
        "adapted_script": (
            "Automatizar más no es el primer paso. "
            "Primero tienes que ver qué repites [X]. "
            "Si no lo puedes decir en voz alta, no lo pongas en un flujo. "
            "El orden es observar, quitar y solo después automatizar."
        ),
    },
    "EJEMPLOGR4": {
        "hook_type": "historia personal",
        "hook": "El día que dejé de perseguir herramientas nuevas.",
        "transcript": (
            "El día que dejé de perseguir herramientas nuevas. "
            "Tenía una lista enorme y cero hábitos. "
            "El cambio fue quedarme con un flujo que sí uso. "
            "Desde entonces empiezo la semana con ese flujo y no con otra app."
        ),
        "blocks": [
            {"id": "gancho", "texto": "El día que dejé de [impulso reconocible].", "en_pantalla": "A cámara, tono de historia."},
            {"id": "contexto", "texto": "Tenía [X] y cero [hábito].", "en_pantalla": "[X]"},
            {"id": "conflicto", "texto": "La lista crecía y el trabajo no.", "en_pantalla": "[X]"},
            {"id": "giro", "texto": "El cambio fue quedarme con [X].", "en_pantalla": "[X]"},
            {"id": "cierre", "texto": "Esta semana empieza por [X], no por otra app.", "en_pantalla": "Cierre a cámara."},
        ],
        "template": (
            "Gancho: El día que dejé de [impulso reconocible].\n"
            "Contexto: Tenía [X] y cero [hábito].\n"
            "Conflicto: La lista crecía y el trabajo no.\n"
            "Giro: El cambio fue quedarme con [X].\n"
            "Cierre: Esta semana empieza por [X], no por otra app."
        ),
        "adapted_script": (
            "El día que dejé de perseguir herramientas nuevas. "
            "Tenía [X] y cero hábitos. "
            "El cambio fue quedarme con un flujo que sí uso para [X]. "
            "Desde entonces empiezo la semana con ese flujo y no con otra app."
        ),
    },
}


def load_example_payload() -> dict:
    return json.loads(EXAMPLE_ITEMS.read_text(encoding="utf-8"))


def seed_examples(db: Database) -> None:
    for username in WATCHLIST:
        db.upsert_account(username, active=True)
    payload = load_example_payload()
    if payload.get("example") is not True:
        raise RuntimeError("La fixture de ejemplo no está marcada como example.")
    parsed = parse_payload(payload)
    if parsed.errors:
        raise RuntimeError("La fixture de ejemplo no se puede leer: " + "; ".join(parsed.errors))
    for reel in parsed.reels:
        if not reel.owner_username:
            raise RuntimeError(f"{reel.instagram_id} no tiene cuenta.")
        account_id = db.ensure_account(reel.owner_username)
        reel_id = db.upsert_reel(account_id, reel, is_example=True, run_id=None)
        content = EXAMPLE_CONTENT.get(reel.short_code or "")
        if content:
            db.save_transcript(
                reel_id,
                text=content["transcript"],
                language="es",
                hook_text=content["hook"],
                segments=[{"start": 0.0, "end": 2.8, "text": content["hook"]}],
                model="fixture",
            )
            db.save_analysis(
                reel_id,
                hook_type=content["hook_type"],
                blocks=content["blocks"],
                template=content["template"],
                adapted_script=content["adapted_script"],
                warnings=[],
                provider="ejemplo",
                model="fixture",
                raw=None,
            )
    db.recompute_outliers()


def seed_if_needed(db: Database) -> bool:
    """Siembra una sola vez, y solo cuando todavía no hay cuentas."""
    from radar.tiktok.csvio import importar_semilla_si_falta

    importar_semilla_si_falta(db)
    if db.get_meta("seeded") == "1":
        return False
    if db.count_accounts() == 0:
        seed_examples(db)
    db.set_meta("seeded", "1")
    return True
