"""Subnicho e idioma. El modelo propone; si falla, manda la heurística.

Una cuenta entra en el radar si el nicho queda en IA o en Negocio con IA.
«fuera» es un descarte, no una etiqueta para rellenar a ciegas.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from radar.instagram.reglas import idioma_de, senal_ia

NICHOS = ("IA", "Negocio con IA")
SUBNICHOS = (
    "Herramientas IA",
    "Automatización",
    "Negocio/agencia IA",
    "Productividad",
    "Programación/vibe coding",
    "Noticias IA",
    "Agentes IA",
    "Ganar dinero con IA",
    "IA para solopreneurs",
)

_REGLAS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Negocio con IA", "Negocio/agencia IA", ("agencia ia", "ai agency", "primer cliente", "first client")),
    ("Negocio con IA", "Ganar dinero con IA", ("ganar dinero", "make money", "monetiz")),
    ("Negocio con IA", "IA para solopreneurs", ("solopreneur", "solo founder", "emprendedor")),
    ("IA", "Automatización", ("automatiz", "automation", "n8n", "zapier", "make.com")),
    ("IA", "Agentes IA", ("agentes de ia", "agente de ia", "ai agent", "claude code", "agentes ia")),
    ("IA", "Programación/vibe coding", ("vibe cod", "vibecoding", "programaci", "cursor")),
    ("IA", "Noticias IA", ("noticia", "news", "explained", "últim", "latest in ai")),
    ("IA", "Productividad", ("productiv", "prompt", "chatgpt", "claude")),
    ("Negocio con IA", "Negocio/agencia IA", ("negocio con ia", "ai business", "founder", "para empresas")),
    ("IA", "Herramientas IA", ("herramienta", "ai tool", "tutorial", "tips")),
)


@dataclass
class Etiqueta:
    nicho: str
    subnicho: str
    idioma: str
    clasificacion: str

    @property
    def en_nicho(self) -> bool:
        return self.nicho in NICHOS


class Completador(Protocol):
    def complete(self, system: str, user: str) -> str: ...


def _sistema() -> str:
    sub = ", ".join(SUBNICHOS)
    return (
        "Clasificas creadores de Instagram del nicho de inteligencia artificial "
        "y de negocios que se construyen con IA. Respondes solo con un objeto JSON, "
        "en español de España.\n"
        "No inventes seguidores, reproducciones, fechas ni enlaces que no estén en el texto.\n"
        "nicho: «IA», «Negocio con IA» o «fuera» si el texto no es de ese nicho "
        "(cocina, moda, deporte, SEO puro, humor sin IA).\n"
        f"subnicho: uno de estos, o una etiqueta corta en español del mismo estilo: {sub}. "
        "Si nicho es «fuera», deja subnicho vacío.\n"
        "idioma: «es» si hay español (aunque esté mezclado), «en» si el texto es inglés, "
        "u otro código de dos letras si se ve claro. Si no puedes decirlo, déjalo vacío."
    )


def _usuario(*, handle: str, nombre: str, bio: str, enlace: str, pies: str) -> str:
    return (
        "Devuelve JSON con esta forma:\n"
        '{"nicho":"", "subnicho":"", "idioma":""}\n\n'
        f"Cuenta: @{handle}\n"
        f"Nombre: {nombre or '(vacío)'}\n"
        f"Bio:\n{(bio or '').strip() or '(vacía)'}\n"
        f"Enlace: {enlace or '(ninguno)'}\n"
        f"Pies de los reels leídos:\n{(pies or '').strip() or '(ninguno)'}"
    )


def _cargar_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("no es un objeto")
    return data


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    return str(valor).strip()


def _nicho_canonico(valor: str) -> str:
    texto = (valor or "").strip().casefold()
    if texto in {"fuera", "off", "no", "otro", "otros"}:
        return "fuera"
    if texto in {"ia", "ai", "inteligencia artificial"}:
        return "IA"
    if "negocio" in texto or "agencia" in texto or "solopreneur" in texto:
        return "Negocio con IA"
    if "ia" in texto or "ai" in texto or "inteligencia" in texto:
        return "IA"
    return ""


def _idioma_canonico(valor: str, respaldo: str) -> str:
    texto = (valor or "").strip().casefold().replace("_", "-")
    if texto.startswith("es"):
        return "es"
    if texto.startswith("en"):
        return "en"
    if re.fullmatch(r"[a-z]{2}", texto):
        return texto
    return respaldo if respaldo in {"es", "en"} else ""


def heuristica(*, handle: str, nombre: str, bio: str, enlace: str, pies: str, idioma: str) -> Etiqueta:
    texto = f"{handle}\n{nombre}\n{bio}\n{enlace}\n{pies}"
    idioma_final = idioma if idioma in {"es", "en"} else idioma_de(texto)
    if not senal_ia(handle, texto):
        return Etiqueta(
            nicho="fuera",
            subnicho="",
            idioma=idioma_final,
            clasificacion="heurística por palabras clave: REVISAR",
        )
    plegado = texto.casefold()
    nicho, subnicho = "IA", "Herramientas IA"
    for candidato, sub, claves in _REGLAS:
        if any(clave in plegado for clave in claves):
            nicho, subnicho = candidato, sub
            break
    return Etiqueta(
        nicho=nicho,
        subnicho=subnicho,
        idioma=idioma_final,
        clasificacion="heurística por palabras clave: REVISAR",
    )


def etiquetar_con_modelo(
    cliente: Completador,
    *,
    handle: str,
    nombre: str,
    bio: str,
    enlace: str,
    pies: str,
    idioma: str,
    proveedor: str,
) -> Etiqueta:
    bruto = cliente.complete(
        _sistema(),
        _usuario(handle=handle, nombre=nombre, bio=bio, enlace=enlace, pies=pies),
    )
    data = _cargar_json(bruto)
    nicho = _nicho_canonico(_texto(data.get("nicho")))
    subnicho = _texto(data.get("subnicho"))
    idioma_modelo = _idioma_canonico(_texto(data.get("idioma")), idioma)
    if nicho == "fuera":
        subnicho = ""
    elif nicho not in NICHOS:
        return heuristica(handle=handle, nombre=nombre, bio=bio, enlace=enlace, pies=pies, idioma=idioma_modelo or idioma)
    if not subnicho and nicho in NICHOS:
        subnicho = heuristica(
            handle=handle, nombre=nombre, bio=bio, enlace=enlace, pies=pies, idioma=idioma_modelo or idioma
        ).subnicho
    return Etiqueta(
        nicho=nicho,
        subnicho=subnicho,
        idioma=idioma_modelo,
        clasificacion=f"llm ({proveedor})",
    )


class Clasificador:
    """Intenta el LLM configurado. Si no hay, o falla, usa la heurística."""

    def __init__(self, cliente: Completador | None, proveedor: str) -> None:
        self.cliente = cliente
        self.proveedor = proveedor
        self.llm_apagado = False

    def clasificar(
        self,
        *,
        handle: str,
        nombre: str,
        bio: str,
        enlace: str,
        pies: str,
        idioma: str,
    ) -> Etiqueta:
        if self.cliente is not None and not self.llm_apagado:
            try:
                return etiquetar_con_modelo(
                    self.cliente,
                    handle=handle,
                    nombre=nombre,
                    bio=bio,
                    enlace=enlace,
                    pies=pies,
                    idioma=idioma,
                    proveedor=self.proveedor,
                )
            except Exception as exc:  # noqa: BLE001 - cualquier fallo del modelo cae a la heurística
                from radar.llm_codex import CodexRateLimit

                if isinstance(exc, CodexRateLimit):
                    self.llm_apagado = True
        return heuristica(handle=handle, nombre=nombre, bio=bio, enlace=enlace, pies=pies, idioma=idioma)

    def close(self) -> None:
        cerrar = getattr(self.cliente, "close", None)
        if cerrar:
            cerrar()


def clasificador_desde_ajustes(settings, *, sin_llm: bool = False) -> Clasificador:
    if sin_llm or settings.llm_provider == "none" or not settings.llm_ready:
        return Clasificador(None, "ninguno")
    from radar.llm_codex import client_for_settings

    return Clasificador(client_for_settings(settings), settings.llm_provider)
