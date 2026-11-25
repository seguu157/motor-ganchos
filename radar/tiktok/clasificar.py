"""Nicho, subnicho y qué vende. El modelo propone; si falla, manda la heurística."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from radar.tiktok.reglas import host_de_enlace

# La taxonomía sale del corte de cuentas que ya pasan. El subnicho puede afinar.
NICHOS = (
    "Relaciones y citas",
    "Salud mental y bienestar",
    "Autodesarrollo femenino",
    "Belleza",
    "Salud y fitness",
    "Autodesarrollo",
    "Estudio y educación",
    "Espiritualidad y astrología",
    "Moda y estilo",
    "Autodesarrollo masculino",
    "Creadores y marketing",
    "Finanzas personales",
    "Hogar y decoración",
    "Lifestyle",
    "Productividad",
    "Maternidad",
)

_REGLAS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Creadores y marketing", "Crecer en TikTok", ("grow now", "goviral", "tiktok growth", "crecer en tiktok")),
    ("Espiritualidad y astrología", "Astrología IA", ("astrology", "astrolog", "easystars", "lunaris", "horoscope", "zodiac")),
    ("Relaciones y citas", "Apps de citas", ("coffee meets bagel", "dating app", "app de citas")),
    ("Relaciones y citas", "Consejos de pareja", ("relationship", "pareja", "vent now", "justly", "breakup", "ruptura")),
    ("Salud mental y bienestar", "TDAH", ("adhd", "tdah")),
    ("Salud mental y bienestar", "Journaling", ("journal", "diario", "sadness book", "self-love")),
    ("Salud mental y bienestar", "Salud mental", ("mental health", "anxiety", "mindfulness", "ansiedad", "terapia")),
    ("Belleza", "Pestañas", ("lash", "pestaña")),
    ("Belleza", "Pelo", ("hair", "pelo", "cabello")),
    ("Belleza", "Skincare", ("skincare", "pore", "serum", "piel")),
    ("Belleza", "Belleza", ("beauty", "makeup", "glow up")),
    ("Salud y fitness", "Nutrición", ("meal plan", "nutrition", "dieta", "calorie", "receta")),
    ("Salud y fitness", "Gym", ("gym", "workout", "fitness", "entreno")),
    ("Estudio y educación", "Apps de estudio", ("study", "estudi", "feynman", "studley", "notas")),
    ("Estudio y educación", "Planners", ("planner", "examen")),
    ("Finanzas personales", "Presupuesto", ("budget", "presupuesto", "finanz", "dinero")),
    ("Hogar y decoración", "Interiores", ("home decor", "interior", "homedecor")),
    ("Moda y estilo", "Moda", ("outfit", "fashion", "ropa", "shopmy")),
    ("Productividad", "Productividad", ("notion", "productiv", "lifestack")),
    ("Autodesarrollo femenino", "Guías / ebooks", ("becoming her", "soft girl", "feminine", "that girl", "femme")),
    ("Autodesarrollo masculino", "Hábitos / dinero", ("masculine", "status", "level up")),
    ("Maternidad", "Maternidad", ("mom", "madre", "matern", "crunchy")),
    ("Autodesarrollo", "Ebooks", ("ebook", "gumroad", "payhip")),
    ("Lifestyle", "Lifestyle", ("amazon finds", "affiliate")),
)


@dataclass
class Etiqueta:
    nicho: str
    subnicho: str
    que_vende: str
    tipo_producto: str
    idioma: str
    clasificacion: str


class Completador(Protocol):
    def complete(self, system: str, user: str) -> str: ...


def _sistema() -> str:
    lista = ", ".join(NICHOS)
    return (
        "Clasificas cuentas de TikTok que publican slideshows y venden algo. "
        "Respondes solo con un objeto JSON, en español de España.\n"
        "No inventes seguidores, vistas, fechas ni enlaces que no estén en el texto.\n"
        "Si no se ve qué vende, deja que_vende y tipo_producto vacíos.\n"
        f"El nicho tiene que ser uno de estos, o el más cercano: {lista}.\n"
        "tipo_producto: app, producto digital, ecommerce (tienda), ecommerce (afiliados) "
        "u otra etiqueta corta del mismo estilo.\n"
        "idioma: es o en."
    )


def _usuario(*, handle: str, bio: str, enlace: str, pies: str, tipo: str) -> str:
    return (
        "Devuelve JSON con esta forma:\n"
        '{"nicho":"", "subnicho":"", "que_vende":"", "tipo_producto":"", "idioma":""}\n\n'
        f"Cuenta: @{handle}\n"
        f"Bio:\n{(bio or '').strip() or '(vacía)'}\n"
        f"Enlace: {enlace or '(ninguno)'}\n"
        f"Pies de la muestra:\n{(pies or '').strip() or '(ninguno)'}\n"
        f"Señal heurística de producto (puedes corregirla): {tipo or '(ninguna)'}"
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


def heuristica(
    *,
    handle: str,
    bio: str,
    enlace: str,
    pies: str,
    tipo_producto: str,
    idioma: str,
) -> Etiqueta:
    texto = f"{handle}\n{bio}\n{enlace}\n{pies}".casefold()
    nicho, subnicho = "Sin clasificar", ""
    for candidato, sub, claves in _REGLAS:
        if any(clave in texto for clave in claves):
            nicho, subnicho = candidato, sub
            break
    host = host_de_enlace(enlace)
    if tipo_producto and host:
        que_vende = f"{host} ({tipo_producto})"
    elif tipo_producto:
        que_vende = tipo_producto
    else:
        que_vende = ""
    return Etiqueta(
        nicho=nicho,
        subnicho=subnicho,
        que_vende=que_vende,
        tipo_producto=tipo_producto,
        idioma=idioma if idioma in {"es", "en"} else "",
        clasificacion="heurística por palabras clave: REVISAR",
    )


def etiquetar_con_modelo(
    cliente: Completador,
    *,
    handle: str,
    bio: str,
    enlace: str,
    pies: str,
    tipo_producto: str,
    idioma: str,
    proveedor: str,
) -> Etiqueta:
    bruto = cliente.complete(
        _sistema(),
        _usuario(handle=handle, bio=bio, enlace=enlace, pies=pies, tipo=tipo_producto),
    )
    data = _cargar_json(bruto)
    nicho = _texto(data.get("nicho")) or "Sin clasificar"
    subnicho = _texto(data.get("subnicho"))
    que_vende = _texto(data.get("que_vende"))
    tipo = _texto(data.get("tipo_producto")) or tipo_producto
    idioma_modelo = _texto(data.get("idioma")).casefold()
    if idioma_modelo not in {"es", "en"}:
        idioma_modelo = idioma if idioma in {"es", "en"} else ""
    if not que_vende and tipo:
        que_vende = tipo
    return Etiqueta(
        nicho=nicho,
        subnicho=subnicho,
        que_vende=que_vende,
        tipo_producto=tipo,
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
        bio: str,
        enlace: str,
        pies: str,
        tipo_producto: str,
        idioma: str,
    ) -> Etiqueta:
        if self.cliente is not None and not self.llm_apagado:
            try:
                return etiquetar_con_modelo(
                    self.cliente,
                    handle=handle,
                    bio=bio,
                    enlace=enlace,
                    pies=pies,
                    tipo_producto=tipo_producto,
                    idioma=idioma,
                    proveedor=self.proveedor,
                )
            except Exception as exc:  # noqa: BLE001 - cualquier fallo del modelo cae a la heurística
                from radar.llm_codex import CodexRateLimit

                if isinstance(exc, CodexRateLimit):
                    self.llm_apagado = True
        return heuristica(
            handle=handle,
            bio=bio,
            enlace=enlace,
            pies=pies,
            tipo_producto=tipo_producto,
            idioma=idioma,
        )

    def close(self) -> None:
        cerrar = getattr(self.cliente, "close", None)
        if cerrar:
            cerrar()


def clasificador_desde_ajustes(settings, *, sin_llm: bool = False) -> Clasificador:
    if sin_llm or settings.llm_provider == "none" or not settings.llm_ready:
        return Clasificador(None, "ninguno")
    from radar.llm_codex import client_for_settings

    return Clasificador(client_for_settings(settings), settings.llm_provider)
