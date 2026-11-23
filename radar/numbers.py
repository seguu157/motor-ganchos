"""Avisa si un texto generado mete cifras que no estaban en la fuente.

No bloquea el guardado: deja el aviso visible para que Antonio lo revise.
No intenta adivinar si un punto es decimal o de millares; compara el token,
su forma con la coma/punto invertidos y los dígitos pelados.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\d+(?:[.,]\d+)*")


def unexpected_numbers(source: str, generated: str) -> list[str]:
    allowed: set[str] = set()
    for token in _TOKEN.findall(source or ""):
        allowed |= _forms(token)
    found: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN.findall(generated or ""):
        if token in seen:
            continue
        seen.add(token)
        if _forms(token).isdisjoint(allowed):
            found.append(token)
    return found


def _forms(token: str) -> set[str]:
    forms = {token, re.sub(r"\D", "", token)}
    if "," in token and "." not in token:
        forms.add(token.replace(",", "."))
    if "." in token and "," not in token:
        forms.add(token.replace(".", ","))
    return {form for form in forms if form}
