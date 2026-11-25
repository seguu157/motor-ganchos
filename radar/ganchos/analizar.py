"""Análisis con el mismo backend que el resto de Radar. Una pieza cada vez.

Si Codex llega al límite, lo ya analizado se queda y el resto sigue pendiente.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from radar.config import Settings
from radar.db import Database
from radar.ganchos.store import borrar_analisis, guardar_analisis, pendientes_de_analisis
from radar.llm import HOOK_TYPES, LLMError, _load_json
from radar.llm_codex import CodexNotInstalled, CodexNotLoggedIn, CodexRateLimit, client_for_settings
from radar.numbers import unexpected_numbers
from radar.texto import plegar

FORMATOS = (
    "lista",
    "antes y después",
    "historia en primera persona",
    "cosas que me hubiera gustado saber",
    "mito y verdad",
    "tutorial",
    "comparación",
    "demostración",
    "pregunta",
    "error común",
    "otro",
)

_ALIASES = {
    "listicle": "lista",
    "lista o pasos": "lista",
    "before/after": "antes y después",
    "antes/despues": "antes y después",
    "antes y despues": "antes y después",
    "pov": "historia en primera persona",
    "pov story": "historia en primera persona",
    "historia": "historia en primera persona",
    "things i wish i knew": "cosas que me hubiera gustado saber",
    "myth vs truth": "mito y verdad",
    "mito contra verdad": "mito y verdad",
    "comparison": "comparación",
    "comparacion": "comparación",
    "demo": "demostración",
    "demostracion": "demostración",
    "error comun": "error común",
}

_SISTEMA = """Eres el editor de ganchos de Antonio, creador de Almería.
Analizas piezas que ya fueron virales: slideshows de TikTok y reels de Instagram.
Respondes solo con un objeto JSON, en español de España y de tú.

Reglas:
- No inventes cifras, porcentajes, dinero, fechas, seguidores ni resultados.
- Si una cifra no está escrita en el material, escribe [X].
- No copies las reproducciones ni el múltiplo al gancho ni a la plantilla.
- La adaptación es una sugerencia para el nicho de Antonio. Empieza por «Sugerencia:».
- Donde haga falta su experiencia, escribe [X].
- hook_type tiene que ser uno de: pregunta, afirmación contraintuitiva, lista, historia personal, demostración, error común, comparación, noticia, otro.
- formato tiene que ser uno de: lista, antes y después, historia en primera persona, cosas que me hubiera gustado saber, mito y verdad, tutorial, comparación, demostración, pregunta, error común, otro.
"""


@dataclass
class Informe:
    status: str
    mensaje: str
    analizados: int = 0
    pendientes: int = 0
    parado: bool = False
    avisos: list[str] = field(default_factory=list)


def analizar(
    db: Database,
    settings: Settings,
    *,
    permitir: bool,
    maximo: int | None = None,
    cliente=None,
    dormir=None,
) -> Informe:
    pendientes = pendientes_de_analisis(db)
    if maximo is not None:
        pendientes = pendientes[: max(maximo, 0)]
    if not permitir:
        return Informe(
            status="plan",
            mensaje=(
                f"Pendientes de analizar: {len(pendientes)}. "
                "Sin --yes no se llama al modelo. Lo ya analizado se salta."
            ),
            pendientes=len(pendientes),
        )
    if not pendientes:
        return Informe(status="ok", mensaje="No hay piezas pendientes de analizar.")
    if settings.llm_provider == "none":
        return Informe(status="ok", mensaje="LLM desactivado (RADAR_LLM=none). No se ha analizado nada.", pendientes=len(pendientes))
    if settings.llm_provider in {"openai", "anthropic"} and not settings.llm_ready and cliente is None:
        return Informe(status="error", mensaje="Falta LLM_MODEL o la clave del proveedor.", pendientes=len(pendientes))

    propio = False
    client = cliente
    if client is None:
        client = client_for_settings(settings)
        propio = True
    espera = dormir or _dormir
    pausa = max(0.0, settings.llm_call_delay) if settings.llm_provider == "codex" else 0
    perfil = db.get_perfil()
    hechos = 0
    try:
        for indice, pieza in enumerate(pendientes):
            if indice and pausa > 0:
                espera(pausa)
            try:
                bruto = client.complete(*_prompt(pieza, perfil))
                datos = parsear_analisis(bruto, fuente=_fuente_de(pieza))
            except (CodexRateLimit, CodexNotInstalled, CodexNotLoggedIn) as exc:
                return Informe(
                    status="parado",
                    mensaje=f"Analizadas {hechos}. {exc} La próxima ejecución retoma las que faltan.",
                    analizados=hechos,
                    pendientes=len(pendientes) - hechos,
                    parado=True,
                )
            except LLMError as exc:
                borrar_analisis(db, int(pieza["id"]))
                guardar_analisis(db, int(pieza["id"]), {"error": str(exc), "analizado_en": ""})
                continue
            datos["provider"] = settings.llm_provider
            datos["model"] = settings.llm_model or settings.codex_model or ""
            guardar_analisis(db, int(pieza["id"]), datos)
            hechos += 1
    finally:
        cerrar = getattr(client, "close", None)
        if propio and cerrar:
            cerrar()
    return Informe(status="ok", mensaje=f"Analizadas {hechos}.", analizados=hechos, pendientes=len(pendientes) - hechos)


def parsear_analisis(bruto: str, *, fuente: str) -> dict:
    data = _load_json(bruto or "")
    hook = _texto(data.get("hook") or data.get("gancho"))
    formato = normalizar_formato(data.get("formato") or data.get("format"))
    plantilla = _texto(data.get("plantilla") or data.get("template")) or "[X]"
    adaptacion = _texto(data.get("adaptacion") or data.get("sugerencia"))
    if adaptacion and not plegar(adaptacion).startswith("sugerencia"):
        adaptacion = f"Sugerencia: {adaptacion}"
    elif not adaptacion:
        adaptacion = "Sugerencia: [X]"
    por_que = _texto(data.get("por_que") or data.get("why"))
    avisos = []
    for etiqueta, texto in (("la plantilla", plantilla), ("la adaptación", adaptacion), ("el porqué", por_que)):
        for numero in unexpected_numbers(fuente, texto):
            avisos.append(
                f"{etiqueta.capitalize()} incluye la cifra {numero}, que no está en la pieza. Revísala o cámbiala por [X]."
            )
    return {
        "hook": hook,
        "hook_type": _tipo(data.get("hook_type") or data.get("tipo")),
        "formato": formato,
        "estructura": _texto(data.get("estructura") or data.get("structure")),
        "insercion": _texto(data.get("insercion_producto") or data.get("insercion") or data.get("producto")),
        "angulo": _texto(data.get("angulo") or data.get("emocion")),
        "por_que": por_que,
        "plantilla": plantilla,
        "adaptacion": adaptacion,
        "warnings": avisos,
        "raw": bruto,
        "error": "",
    }


def normalizar_formato(valor) -> str:
    texto = plegar(str(valor or ""))
    if not texto:
        return "otro"
    if texto in {plegar(item) for item in FORMATOS}:
        for item in FORMATOS:
            if plegar(item) == texto:
                return item
    return _ALIASES.get(texto, "otro")


def _tipo(valor) -> str:
    texto = plegar(str(valor or ""))
    for nombre in HOOK_TYPES:
        if plegar(nombre) == texto:
            return nombre
    return "otro"


def _prompt(pieza: dict, perfil) -> tuple[str, str]:
    diapositivas = pieza.get("diapositivas") or []
    lineas = []
    for slide in diapositivas:
        if not isinstance(slide, dict):
            continue
        texto = str(slide.get("texto") or "").strip()
        if texto:
            lineas.append(f"Diapositiva {slide.get('indice')}: {texto}")
    usuario = "\n".join(
        [
            "Devuelve JSON con esta forma:",
            json.dumps(
                {
                    "hook": "gancho literal o plantilla corta",
                    "hook_type": "uno de la lista",
                    "formato": "uno de la lista",
                    "estructura": "cómo está armada la pieza, en una frase",
                    "insercion_producto": "en qué momento entra el producto, o [X]",
                    "angulo": "ángulo emocional, sin cifras nuevas",
                    "por_que": "por qué encaja con el material, sin inventar resultados",
                    "plantilla": "plantilla reutilizable con [X]",
                    "adaptacion": "Sugerencia: cómo la adaptaría Antonio, con [X]",
                },
                ensure_ascii=False,
            ),
            "",
            f"Fuente: {pieza.get('fuente')}",
            f"Cuenta: @{pieza.get('handle')}",
            f"URL: {pieza.get('url')}",
            f"Nicho guardado: {pieza.get('nicho') or '[X]'}",
            f"Subnicho guardado: {pieza.get('subnicho') or '[X]'}",
            f"Vistas guardadas: {pieza.get('vistas') if pieza.get('vistas') is not None else '[X]'}",
            f"Mediana guardada: {pieza.get('mediana') if pieza.get('mediana') is not None else '[X]'}",
            f"Múltiplo guardado: {pieza.get('multiplo') if pieza.get('multiplo') is not None else '[X]'}",
            f"Pie: {pieza.get('caption') or '(sin pie)'}",
            f"Gancho ya leído: {pieza.get('gancho_texto') or '(vacío)'}",
            f"Texto en pantalla: {pieza.get('texto_pantalla') or '(vacío)'}",
            "Diapositivas:",
            "\n".join(lineas) or "(no hay diapositivas)",
            "Transcripción:",
            pieza.get("transcripcion") or "(sin transcripción)",
            "",
            "Perfil de Antonio (si un campo es [X], no lo completes):",
            f"Oferta: {perfil.hueco('oferta')}",
            f"Audiencia: {perfil.hueco('audiencia')}",
            f"Problema: {perfil.hueco('problema')}",
            f"CTA: {perfil.hueco('cta')}",
            f"Nicho: {perfil.hueco('nicho')}",
            f"Tono: {perfil.hueco('tono')}",
            f"Pruebas que sí se pueden citar: {perfil.hueco('pruebas')}",
            f"Restricciones: {perfil.hueco('restricciones')}",
        ]
    )
    return _SISTEMA, usuario


def _fuente_de(pieza: dict) -> str:
    partes = [
        str(pieza.get("caption") or ""),
        str(pieza.get("gancho_texto") or ""),
        str(pieza.get("texto_pantalla") or ""),
        str(pieza.get("transcripcion") or ""),
        str(pieza.get("vistas") if pieza.get("vistas") is not None else ""),
        str(pieza.get("mediana") if pieza.get("mediana") is not None else ""),
        str(pieza.get("multiplo") if pieza.get("multiplo") is not None else ""),
    ]
    for slide in pieza.get("diapositivas") or []:
        if isinstance(slide, dict):
            partes.append(str(slide.get("texto") or ""))
    return "\n".join(partes)


def _texto(valor) -> str:
    if valor is None:
        return ""
    return str(valor).strip()


def _dormir(segundos: float) -> None:
    import time

    time.sleep(segundos)
