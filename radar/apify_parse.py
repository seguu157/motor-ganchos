"""Parser de la respuesta de apify/instagram-reel-scraper.

Acepta el JSON que devuelve el dataset del actor (lista de objetos) y también
un envoltorio `{"items": [...]}` usado por las fixtures. Lee los nombres
oficiales (`videoPlayCount`, `likesCount`, `commentsCount`, `timestamp`,
`caption`, `url`, `videoUrl`, `audioUrl`) y, si faltan, los alias cortos
`likes`, `comments` y `date`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class ParseError(ValueError):
    """Un elemento del dataset no se puede leer."""


@dataclass(frozen=True)
class ParsedReel:
    instagram_id: str
    short_code: str | None
    url: str
    caption: str
    published_at: str | None
    likes_count: int | None
    comments_count: int | None
    video_play_count: int | None
    video_view_count: int | None
    video_url: str | None
    audio_url: str | None
    video_duration: float | None
    owner_username: str | None
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class ParseResult:
    reels: list[ParsedReel]
    errors: list[str]


def parse_payload(payload: Any) -> ParseResult:
    items = _unwrap(payload)
    reels: list[ParsedReel] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"Elemento {index}: no es un objeto.")
            continue
        try:
            reels.append(parse_item(item))
        except ParseError as exc:
            errors.append(f"Elemento {index}: {exc}")
    return ParseResult(reels=reels, errors=errors)


def parse_item(item: dict[str, Any]) -> ParsedReel:
    short_code = _text(_pick(item, "shortCode", "shortcode"))
    url = _text(_pick(item, "url"))
    instagram_id = _text(_pick(item, "id")) or short_code or url
    if not instagram_id:
        raise ParseError("falta id, shortCode y url.")
    if not url and short_code:
        url = f"https://www.instagram.com/reel/{short_code}/"
    if not url:
        raise ParseError("falta url.")

    owner = _text(_pick(item, "ownerUsername", "owner_username"))
    if owner:
        owner = owner.lstrip("@").lower()

    return ParsedReel(
        instagram_id=instagram_id,
        short_code=short_code,
        url=url,
        caption=_text(_pick(item, "caption")) or "",
        published_at=_text(_pick(item, "timestamp", "date")),
        likes_count=_as_int(_pick(item, "likesCount", "likes")),
        comments_count=_as_int(_pick(item, "commentsCount", "comments")),
        video_play_count=_as_int(_pick(item, "videoPlayCount")),
        video_view_count=_as_int(_pick(item, "videoViewCount")),
        video_url=_text(_pick(item, "videoUrl")),
        audio_url=_text(_pick(item, "audioUrl")),
        video_duration=_as_float(_pick(item, "videoDuration")),
        owner_username=owner,
        raw=item,
    )


def build_actor_input(usernames: list[str], results_limit: int) -> dict[str, Any]:
    """Entrada del actor: metadatos, sin add-ons de pago.

    `includeTranscript`, `includeDownloadedVideo` e `includeSharesCount`
    disparan cargos aparte. El audio para transcribir en local llega en
    `audioUrl` / `videoUrl` de la salida normal, sin descargar el MP4 en Apify.
    """
    if results_limit < 1:
        raise ValueError("resultsLimit tiene que ser al menos 1.")
    clean = [name.strip().lstrip("@") for name in usernames if name and name.strip()]
    if not clean:
        raise ValueError("No hay usuarios para pedir.")
    return {
        "username": clean,
        "resultsLimit": results_limit,
        "skipPinnedPosts": False,
        "skipTrialReels": False,
        "includeSharesCount": False,
        "includeTranscript": False,
        "includeDownloadedVideo": False,
    }


def _unwrap(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            return payload["items"]
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        return [payload]
    raise ParseError("La respuesta no es una lista ni un objeto con items.")


def _pick(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value < 0 or not value.is_integer():
            return None
        return int(value)
    if isinstance(value, str):
        stripped = value.strip().replace(" ", "")
        if re.fullmatch(r"\d+", stripped):
            return int(stripped)
        return None
    return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if isinstance(value, str):
        stripped = value.strip().replace(",", ".")
        try:
            number = float(stripped)
        except ValueError:
            return None
        return number if number >= 0 else None
    return None
