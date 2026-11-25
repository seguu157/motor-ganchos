"""Viralidad de reels y señales de nicho.

La mediana sale de hasta 20 reels recientes no fijados que traen
reproducciones. Un reel es viral si no está fijado, llega al doble de esa
mediana y, además, a un suelo absoluto. Un fijado no cuenta como viral ni
puede ser la mejor pieza. La cuenta pasa si hay al menos uno y el texto es de IA.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from radar.usernames import UsernameError, normalize_username

VISTAS_MINIMAS = 50_000
UMBRAL = 2.0
RECIENTES = 20

_MENCION = re.compile(r"(?<![A-Za-z0-9._])@([A-Za-z0-9._]{1,30})")
_HASHTAG = re.compile(r"(?<![A-Za-z0-9_])#([A-Za-z0-9_]{2,40})")
_EXTRA_RESERVADOS = {
    "about",
    "challenge",
    "developer",
    "directory",
    "emails",
    "facebook",
    "instagram",
    "legal",
    "meta",
    "privacy",
    "reels",
    "www",
}
_IA = re.compile(
    r"\bia\b|inteligencia artificial|(?<![a-z0-9])ai(?![a-z0-9])|chatgpt|\bclaude\b|"
    r"\bgpt\b|openai|midjourney|\bllm\b|vibe ?cod|automatizaci[oó]n|automation|"
    r"\bn8n\b|ai agent|agentes de ia|agente de ia|ai tools|herramientas de ia|"
    r"nocode|no-code|copilot|aiautomation|aitools",
    re.I,
)
_HANDLE_IA = re.compile(
    r"(?:^|[._])ia(?:[._]|$)|(?:^|[._])ai(?:[._]|$)|(?:^|[._])agi(?:[._]|$)|"
    r"gpt|aiagent|aitool|chatgpt",
    re.I,
)
_ES = re.compile(
    r"\b(que|para|con|los|las|una|por|cómo|qué|aquí|herramientas|negocio|dinero|"
    r"te|aprende|sin|más|automatiza)\b",
    re.I,
)
_EN = re.compile(
    r"\b(the|and|you|your|for|with|this|how|what|tools|business|money|learn|build)\b",
    re.I,
)


@dataclass
class Reel:
    code: str
    url: str
    plays: int | None
    caption: str
    taken_at: str | None
    fijado: bool = False
    multiplo: float | None = None


@dataclass
class Evaluacion:
    tiene_viral: bool
    motivo: str
    reels_muestreados: int | None
    fijados: int | None
    mediana_vistas: int | None
    num_piezas_virales: int | None
    num_virales_no_fijadas: int | None
    mejor_pieza_url: str = ""
    mejor_pieza_vistas: int | None = None
    mejor_pieza_multiplo: float | None = None
    mejor_pieza_fijada: str = ""
    piezas_virales: str = ""
    captions: list[str] = field(default_factory=list)
    idioma: str = ""
    mediana_exacta: float | None = None


def normalizar_handle(valor: str) -> str:
    """Usuario de Instagram, en minúsculas. Vacío si no vale."""
    text = (valor or "").strip()
    if not text:
        return ""
    try:
        handle = normalize_username(text)
    except UsernameError:
        return ""
    if handle in _EXTRA_RESERVADOS or handle.strip(".") == "":
        return ""
    return handle


def menciones(texto: str) -> list[str]:
    vistos: list[str] = []
    for crudo in _MENCION.findall(texto or ""):
        handle = normalizar_handle(crudo)
        if handle and handle not in vistos:
            vistos.append(handle)
    return vistos


def hashtags(texto: str) -> list[str]:
    vistos: list[str] = []
    for crudo in _HASHTAG.findall(texto or ""):
        tag = crudo.casefold()
        if tag not in vistos and len(tag) >= 3:
            vistos.append(tag)
    return vistos


def senal_ia(*textos: str) -> bool:
    junto = "\n".join(texto or "" for texto in textos)
    if _IA.search(junto):
        return True
    for texto in textos:
        handle = normalizar_handle(texto or "")
        if handle and _HANDLE_IA.search(handle):
            return True
    return False


def idioma_de(texto: str) -> str:
    es = len(_ES.findall(texto or ""))
    en = len(_EN.findall(texto or ""))
    if es > en:
        return "es"
    if en > es:
        return "en"
    return ""


def entero_vistas(valor) -> int | None:
    """Solo un entero ya leído. «1,2M» o un decimal no se convierten."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if valor >= 0 else None
    if isinstance(valor, float):
        if valor < 0 or not valor.is_integer():
            return None
        return int(valor)
    text = str(valor).strip().replace(" ", "")
    if not text or not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def slugs_de_keyword(keyword: str) -> list[str]:
    palabras = re.findall(r"[a-z0-9]{4,}", (keyword or "").casefold())
    junto = re.sub(r"[^a-z0-9]+", "", (keyword or "").casefold())
    vistos: list[str] = []
    for slug in [*palabras, junto]:
        if len(slug) < 4 or slug in vistos:
            continue
        vistos.append(slug)
    return vistos[:3]


def evaluar(
    reels: list[Reel],
    *,
    bio: str = "",
    handle: str = "",
    privada: bool = False,
    vistas_minimas: int = VISTAS_MINIMAS,
    umbral: float = UMBRAL,
    recientes: int = RECIENTES,
) -> Evaluacion:
    captions = [reel.caption for reel in reels if reel.caption]
    idioma = idioma_de(f"{bio}\n" + "\n".join(captions))
    if privada:
        return Evaluacion(
            tiene_viral=False,
            motivo="cuenta privada",
            reels_muestreados=None,
            fijados=None,
            mediana_vistas=None,
            num_piezas_virales=None,
            num_virales_no_fijadas=None,
            captions=captions,
            idioma=idioma,
        )
    con_plays = [reel for reel in reels if reel.plays is not None]
    fijados = [reel for reel in con_plays if reel.fijado]
    muestra = _recientes(con_plays, recientes)
    plays = [reel.plays for reel in muestra if reel.plays is not None]
    mediana = float(statistics.median(plays)) if plays else None
    universo = list(muestra)
    for reel in universo:
        if mediana and reel.plays is not None and mediana > 0:
            reel.multiplo = round(reel.plays / mediana, 1)
        else:
            reel.multiplo = None
    virales = [
        reel
        for reel in universo
        if reel.plays is not None
        and mediana is not None
        and mediana > 0
        and reel.plays >= umbral * mediana
        and reel.plays >= vistas_minimas
    ]
    vistos: set[str] = set()
    unicos: list[Reel] = []
    for reel in virales:
        if reel.code in vistos:
            continue
        vistos.add(reel.code)
        unicos.append(reel)
    unicos.sort(key=lambda reel: reel.plays or 0, reverse=True)
    no_fijadas = [reel for reel in unicos if not reel.fijado]
    if mediana is None or mediana <= 0:
        motivo = "sin reels recientes con reproducciones"
    elif not unicos:
        solo_multiplo = [
            reel
            for reel in muestra
            if reel.plays is not None and mediana > 0 and reel.plays >= umbral * mediana
        ]
        if not solo_multiplo:
            hay_fijado = any(
                reel.plays is not None and mediana > 0 and reel.plays >= umbral * mediana for reel in fijados
            )
            motivo = "ningún reel no fijado ≥2× mediana" if hay_fijado else "ningún reel ≥2× mediana"
        else:
            tope = max(reel.plays or 0 for reel in solo_multiplo)
            motivo = f"reels ≥2× pero alcance bajo (máx {tope:,} < {vistas_minimas:,})"
    else:
        motivo = ""
    mejor = unicos[0] if unicos else None
    return Evaluacion(
        tiene_viral=bool(unicos),
        motivo=motivo,
        reels_muestreados=len(muestra),
        fijados=len(fijados),
        mediana_vistas=int(round(mediana)) if mediana is not None else None,
        num_piezas_virales=len(unicos),
        num_virales_no_fijadas=len(no_fijadas),
        mejor_pieza_url=mejor.url if mejor else "",
        mejor_pieza_vistas=mejor.plays if mejor else None,
        mejor_pieza_multiplo=mejor.multiplo if mejor else None,
        mejor_pieza_fijada=("sí" if mejor.fijado else "no") if mejor else "",
        piezas_virales=_formatear_piezas(unicos),
        captions=captions,
        idioma=idioma,
        mediana_exacta=mediana,
    )


def _recientes(con_plays: list[Reel], limite: int) -> list[Reel]:
    """Hasta `limite` reels no fijados. Con fecha, los más nuevos."""
    sueltos = [reel for reel in con_plays if not reel.fijado]
    con_fecha = [reel for reel in sueltos if reel.taken_at]
    sin_fecha = [reel for reel in sueltos if not reel.taken_at]
    con_fecha.sort(key=lambda reel: reel.taken_at or "", reverse=True)
    return (con_fecha + sin_fecha)[: max(limite, 0)]


def _formatear_piezas(virales: list[Reel]) -> str:
    trozos: list[str] = []
    for reel in virales:
        marca = " | fijado" if reel.fijado else ""
        vistas = f"{reel.plays:,}" if reel.plays is not None else ""
        trozos.append(f"{reel.url} | {vistas} reproducciones | {reel.multiplo}×{marca}")
    return " ; ".join(trozos)
