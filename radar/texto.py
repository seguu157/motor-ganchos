"""Normalización de texto para buscar sin depender de los acentos."""

from __future__ import annotations

import unicodedata


def plegar(texto: str | None) -> str:
    base = unicodedata.normalize("NFD", texto or "")
    sin_acentos = "".join(caracter for caracter in base if unicodedata.category(caracter) != "Mn")
    return " ".join(sin_acentos.casefold().split())
