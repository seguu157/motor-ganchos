"""Gancho: la primera frase, o lo dicho en los primeros ~3 segundos."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SPACES = re.compile(r"\s+")
_SENTENCE = re.compile(r"(.+?[.!?…])(?:\s|$)")


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


def first_sentence(text: str) -> str:
    cleaned = _SPACES.sub(" ", text or "").strip()
    if not cleaned:
        return ""
    match = _SENTENCE.match(cleaned)
    if not match:
        return cleaned
    return match.group(1).strip()


def text_in_window(segments: list[Segment], window: float = 3.0) -> str:
    parts: list[str] = []
    for segment in segments:
        if segment.start >= window:
            continue
        piece = segment.text.strip()
        if piece:
            parts.append(piece)
    return _SPACES.sub(" ", " ".join(parts)).strip()


def extract_hook(full_text: str, segments: list[Segment] | None = None, window: float = 3.0) -> str:
    """Devuelve la primera frase si cabe en la apertura; si no, el texto de los primeros segundos.

    Sin marcas de tiempo no se puede cortar el audio, así que se usa la primera
    frase entera. Si un segmento empieza antes de la ventana, entra completo:
    faster-whisper no siempre da tiempos por palabra.
    """
    sentence = first_sentence(full_text)
    if not segments:
        return sentence
    early = text_in_window(segments, window)
    if not early:
        return sentence
    if not sentence:
        return early
    norm_early = _norm(early)
    norm_sentence = _norm(sentence)
    if norm_sentence in norm_early or norm_early.startswith(norm_sentence):
        return sentence
    return early


def _norm(text: str) -> str:
    return _SPACES.sub(" ", text).strip().casefold()
