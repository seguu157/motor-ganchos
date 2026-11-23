"""Normaliza usuarios de Instagram pegados como @usuario o como URL."""

from __future__ import annotations

import re

_USERNAME = re.compile(r"[a-z0-9._]{1,30}")
_RESERVED = {"reel", "p", "stories", "explore", "accounts", "direct"}


class UsernameError(ValueError):
    """El texto no es un usuario de Instagram utilizable."""


def normalize_username(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise UsernameError("Escribe un usuario.")

    if "instagram.com" in text.lower() or text.lower().startswith("http"):
        without_query = text.split("?", 1)[0].split("#", 1)[0]
        match = re.search(r"instagram\.com/([^?#]+)", without_query, flags=re.I)
        if not match:
            raise UsernameError("No reconozco esa URL de Instagram.")
        parts = [part for part in match.group(1).split("/") if part]
        if not parts or parts[0].lower() in _RESERVED:
            raise UsernameError("Pega el perfil, no la URL de un reel.")
        text = parts[0]

    if text.startswith("@"):
        text = text[1:]
    text = text.strip().lower()
    if not _USERNAME.fullmatch(text) or text in _RESERVED:
        raise UsernameError("El usuario solo puede tener letras, números, puntos y guiones bajos.")
    return text
