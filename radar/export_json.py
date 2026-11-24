"""Copia de seguridad JSON del banco y de la watchlist. No incluye secretos."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from radar.db import BankEntry, Database
from radar.format_es import format_multiple

EXPORT_VERSION = 1


def export_document(db: Database) -> dict[str, Any]:
    accounts = [
        {"username": row["username"], "active": bool(row["active"]), "notes": row["notes"]}
        for row in db.list_accounts()
    ]
    return {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "note": "Puede incluir filas de ejemplo. Las cifras de ejemplo no son métricas reales.",
        "accounts": accounts,
        "hooks": [_hook(entry) for entry in db.list_bank()],
        "reels": [_reel(row) for row in db.list_reels()],
    }


def dumps_document(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def import_document(db: Database, document: dict[str, Any]) -> int:
    """Restaura cuentas, reels, transcripciones y análisis. No borra lo que no venga."""
    if document.get("version") != EXPORT_VERSION:
        raise ValueError("La copia JSON no es de la versión 1.")
    for account in document.get("accounts") or []:
        db.upsert_account(
            account["username"],
            active=bool(account.get("active", True)),
            notes=account.get("notes") or "",
        )
    saved = 0
    for reel in document.get("reels") or []:
        _import_reel(db, reel)
        saved += 1
    db.recompute_outliers()
    by_url = {row["url"]: row["id"] for row in db.list_reels()}
    for hook in document.get("hooks") or []:
        reel_id = by_url.get(hook.get("url"))
        if reel_id is None:
            continue
        if hook.get("transcript") or hook.get("hook"):
            db.save_transcript(
                reel_id,
                text=hook.get("transcript") or "",
                language="es",
                hook_text=hook.get("hook") or "",
                segments=[],
                model="import",
            )
        if hook.get("template") or hook.get("adapted_script"):
            db.save_analysis(
                reel_id,
                hook_type=hook.get("hook_type") or "otro",
                blocks=hook.get("blocks") or [],
                template=hook.get("template") or "",
                adapted_script=hook.get("adapted_script") or "",
                warnings=hook.get("warnings") or [],
                provider="import",
                model="import",
                raw=None,
            )
    return saved


def filter_entries(
    entries: Iterable[BankEntry],
    *,
    account: str | None = None,
    hook_type: str | None = None,
    min_multiple: float | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    hide_examples: bool = False,
    category: str | None = None,
    media_kind: str | None = None,
    query: str | None = None,
) -> list[BankEntry]:
    from radar.texto import plegar

    needle = plegar(query) if query else ""
    selected: list[BankEntry] = []
    for entry in entries:
        if account and entry.account != account:
            continue
        if hook_type and (entry.hook_type or "") != hook_type:
            continue
        if category and entry.categoria != category:
            continue
        if media_kind and entry.media_kind != media_kind:
            continue
        if needle:
            haystack = plegar(
                " ".join(
                    part
                    for part in (
                        entry.hook,
                        entry.caption,
                        entry.template,
                        entry.adapted_script,
                        entry.account,
                        entry.hook_type,
                        entry.categoria,
                    )
                    if part
                )
            )
            if needle not in haystack:
                continue
        if min_multiple is not None:
            if entry.multiple is None or entry.multiple < min_multiple:
                continue
        day = (entry.published_at or "")[:10]
        if date_from and (not day or day < date_from):
            continue
        if date_to and (not day or day > date_to):
            continue
        if hide_examples and entry.is_example:
            continue
        selected.append(entry)
    return selected


def _hook(entry: BankEntry) -> dict[str, Any]:
    return {
        "account": entry.account,
        "url": entry.url,
        "published_at": entry.published_at,
        "play_count": entry.play_count,
        "median_plays": entry.median_plays,
        "multiple": entry.multiple,
        "multiple_label": format_multiple(entry.multiple) if entry.multiple is not None else None,
        "is_example": entry.is_example,
        "hook_type": entry.hook_type,
        "hook": entry.hook,
        "transcript": entry.transcript,
        "template": entry.template,
        "adapted_script": entry.adapted_script,
        "blocks": entry.blocks,
        "warnings": entry.warnings,
    }


def _reel(row: Any) -> dict[str, Any]:
    return {
        "instagram_id": row["instagram_id"],
        "short_code": row["short_code"],
        "account": row["account"],
        "url": row["url"],
        "caption": row["caption"],
        "published_at": row["published_at"],
        "likes_count": row["likes_count"],
        "comments_count": row["comments_count"],
        "video_play_count": row["video_play_count"],
        "video_view_count": row["video_view_count"],
        "video_url": row["video_url"],
        "audio_url": row["audio_url"],
        "video_duration": row["video_duration"],
        "is_example": bool(row["is_example"]),
    }


def _import_reel(db: Database, reel: dict[str, Any]) -> None:
    from radar.apify_parse import parse_item

    account_id = db.ensure_account(reel["account"])
    parsed = parse_item(
        {
            "id": reel["instagram_id"],
            "shortCode": reel.get("short_code"),
            "url": reel["url"],
            "caption": reel.get("caption") or "",
            "timestamp": reel.get("published_at"),
            "likesCount": reel.get("likes_count"),
            "commentsCount": reel.get("comments_count"),
            "videoPlayCount": reel.get("video_play_count"),
            "videoViewCount": reel.get("video_view_count"),
            "videoUrl": reel.get("video_url"),
            "audioUrl": reel.get("audio_url"),
            "videoDuration": reel.get("video_duration"),
            "ownerUsername": reel["account"],
        }
    )
    db.upsert_reel(account_id, parsed, is_example=bool(reel.get("is_example")), run_id=None)
