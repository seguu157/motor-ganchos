"""Piezas virales ya guardadas. Las fijadas se quedan fuera de la cola."""

from __future__ import annotations

import re
from dataclasses import dataclass

_VISTAS = re.compile(r"([\d][\d.,]*)\s+(?:vistas|reproducciones)", re.I)
_MULTIPLO = re.compile(r"([\d]+(?:[.,]\d+)?)\s*×")
_FIJADA = re.compile(r"(?:^|\|)\s*fijad", re.I)
_VIDEO = re.compile(r"(?:^|\|)\s*vídeo\b", re.I)


@dataclass(frozen=True)
class Candidata:
    fuente: str
    url: str
    handle: str
    nicho: str
    subnicho: str
    vistas: int | None
    mediana: int | None
    multiplo: float | None
    fijada: bool
    slideshow: bool
    que_vende: str
    enlace: str


def parsear_linea(trozo: str) -> tuple[str, int | None, float | None, bool, bool]:
    """URL, vistas, múltiplo, fijada y si es slideshow. No rellena huecos."""
    limpio = (trozo or "").strip()
    url, _, resto = limpio.partition(" | ")
    vistas = _entero(_VISTAS.search(resto))
    multiplo = _decimal(_MULTIPLO.search(resto))
    fijada = bool(_FIJADA.search(resto))
    slideshow = not bool(_VIDEO.search(resto))
    return url.strip(), vistas, multiplo, fijada, slideshow


def candidatas_de_cuentas(cuentas: list[dict], *, fuente: str) -> list[Candidata]:
    piezas: list[Candidata] = []
    vistas: set[str] = set()
    for cuenta in cuentas:
        if (cuenta.get("estado") or "") != "califica":
            continue
        mediana = _mediana(cuenta.get("mediana_vistas"))
        enlace = str(cuenta.get("enlace_bio") or cuenta.get("enlace") or "")
        que_vende = str(cuenta.get("que_vende") or "")
        for trozo in str(cuenta.get("piezas_virales") or "").split(" ; "):
            if not trozo.strip():
                continue
            url, plays, multiplo, fijada, slideshow = parsear_linea(trozo)
            if not url or url in vistas:
                continue
            if fuente == "tiktok" and not slideshow:
                continue
            vistas.add(url)
            piezas.append(
                Candidata(
                    fuente=fuente,
                    url=url,
                    handle=str(cuenta.get("handle") or ""),
                    nicho=str(cuenta.get("nicho") or ""),
                    subnicho=str(cuenta.get("subnicho") or ""),
                    vistas=plays,
                    mediana=mediana,
                    multiplo=multiplo,
                    fijada=fijada,
                    slideshow=slideshow,
                    que_vende=que_vende,
                    enlace=enlace,
                )
            )
    piezas.sort(key=lambda pieza: (-(pieza.multiplo if pieza.multiplo is not None else -1), -(pieza.vistas or -1), pieza.url))
    return piezas


def _mediana(valor) -> int | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor >= 0:
        return int(valor)
    texto = str(valor).strip()
    if texto.isdigit():
        return int(texto)
    return None


def _entero(coincidencia: re.Match[str] | None) -> int | None:
    if coincidencia is None:
        return None
    digitos = re.sub(r"\D", "", coincidencia.group(1))
    if not digitos:
        return None
    return int(digitos)


def _decimal(coincidencia: re.Match[str] | None) -> float | None:
    if coincidencia is None:
        return None
    texto = coincidencia.group(1).replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None
