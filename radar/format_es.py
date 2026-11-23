"""Formato español de cifras, fechas y múltiplos. No altera el dato guardado."""

from __future__ import annotations

import math
from datetime import datetime

_MONTHS = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)


def format_multiple(value: float) -> str:
    """Un decimal y coma: 10.5 -> '10,5x'. Infinito si la mediana era 0."""
    if math.isinf(value):
        return "∞x"
    rounded = round(value, 1)
    return f"{rounded:.1f}x".replace(".", ",")


def format_int_es(value: float | int | None) -> str:
    if value is None:
        return "—"
    number = float(value)
    if not math.isfinite(number):
        return "—"
    if number.is_integer():
        return f"{int(number):,}".replace(",", ".")
    whole, frac = f"{number:.1f}".split(".")
    whole_es = f"{int(whole):,}".replace(",", ".")
    return f"{whole_es},{frac}"


def format_usd(value) -> str:
    quantized = f"{value:.2f}"
    return quantized.replace(".", ",") + " USD"


def format_date(value: str | None) -> str:
    if not value:
        return "—"
    raw = value.strip()
    stamp = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        day = raw[:10]
        return day or raw
    return f"{parsed.day} {_MONTHS[parsed.month - 1]} {parsed.year}"


def date_key(value: str | None) -> str:
    if not value:
        return ""
    return value[:10]
