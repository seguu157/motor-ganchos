"""Exportación e importación del banco en Markdown.

El archivo banco-ganchos.md de la primera entrega no estaba en el repositorio.
Este formato cubre los campos pedidos: creador, URL, múltiplo con coma decimal
(por ejemplo 10,5x), gancho, transcripción, plantilla y guion. Es el contrato
que leen los tests.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from radar.db import BankEntry
from radar.format_es import date_key, format_int_es, format_multiple

_HEADING = re.compile(
    r"^##\s+(?P<multiple>\d+(?:,\d+)?x|∞x)\s+·\s+@(?P<account>[A-Za-z0-9._]+)\s+·\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$"
)
_BULLET = re.compile(r"^- \*\*(?P<label>[^*]+):\*\*\s*(?P<value>.*)$")
_SECTIONS = ("Gancho", "Transcripción", "Formato", "Plantilla", "Guion adaptado")


def render_markdown(entries: Iterable[BankEntry]) -> str:
    rows = list(entries)
    lines = [
        "# Banco de ganchos",
        "",
        "Cada entrada es un reel con reproducciones de al menos 2× la mediana de su cuenta (últimos 20 reels con cifra).",
        "El múltiplo usa coma decimal, por ejemplo 10,5x.",
        "Las filas marcadas como ejemplo no son métricas ni textos reales: son fixtures de demostración.",
        "",
    ]
    if not rows:
        lines.append("_El banco está vacío._")
        lines.append("")
        return "\n".join(lines)

    for entry in rows:
        label = format_multiple(entry.multiple) if entry.multiple is not None else "—x"
        published = date_key(entry.published_at) or "sin-fecha"
        lines.append(f"## {label} · @{entry.account} · {published}")
        lines.append("")
        lines.extend(_bullets(entry, label))
        lines.append("")
        lines.extend(_section("Gancho", entry.hook or "_Pendiente de transcribir._"))
        lines.extend(_section("Transcripción", entry.transcript or "_Pendiente de transcribir._"))
        lines.extend(_section("Formato", _format_blocks(entry)))
        lines.extend(_section("Plantilla", entry.template or "_Pendiente de analizar._"))
        script = entry.adapted_script or "_Pendiente de analizar._"
        lines.extend(_section("Guion adaptado", script))
        if entry.warnings:
            for warning in entry.warnings:
                lines.append(f"> Aviso: {warning}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_markdown(text: str) -> list[dict[str, str]]:
    """Lee el Markdown que escribe `render_markdown`. No interpreta otros formatos."""
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    section: str | None = None
    buffer: list[str] = []

    def flush_section() -> None:
        nonlocal section, buffer
        if current is not None and section is not None:
            current[section] = "\n".join(buffer).strip()
        section = None
        buffer = []

    def flush_entry() -> None:
        nonlocal current
        flush_section()
        if current is not None:
            entries.append(current)
        current = None

    for line in text.splitlines():
        heading = _HEADING.match(line.strip())
        if heading:
            flush_entry()
            current = {
                "multiple_label": heading.group("multiple"),
                "account": heading.group("account"),
                "date": heading.group("date"),
            }
            continue
        if current is None:
            continue
        if line.startswith("### "):
            flush_section()
            title = line[4:].strip()
            if title in _SECTIONS:
                section = title
            continue
        bullet = _BULLET.match(line.strip())
        if bullet and section is None:
            current[bullet.group("label")] = bullet.group("value").strip()
            continue
        if section is not None:
            buffer.append(line)
    flush_entry()
    return entries


def _bullets(entry: BankEntry, label: str) -> list[str]:
    median = format_int_es(entry.median_plays) if entry.median_plays is not None else "—"
    return [
        f"- **Creador:** {entry.account}",
        f"- **URL:** {entry.url}",
        f"- **Fecha:** {date_key(entry.published_at) or '—'}",
        f"- **Múltiplo:** {label}",
        f"- **Reproducciones:** {format_int_es(entry.play_count)}",
        f"- **Mediana de la cuenta:** {median}",
        f"- **Me gusta:** {format_int_es(entry.likes_count)}",
        f"- **Comentarios:** {format_int_es(entry.comments_count)}",
        f"- **Tipo de gancho:** {entry.hook_type or '—'}",
        f"- **Ejemplo:** {'sí' if entry.is_example else 'no'}",
    ]


def _section(title: str, body: str) -> list[str]:
    return [f"### {title}", "", body.strip(), ""]


def _format_blocks(entry: BankEntry) -> str:
    if not entry.blocks:
        return "_Pendiente de analizar._"
    lines = []
    for block in entry.blocks:
        name = str(block.get("id", "")).capitalize()
        text = block.get("texto") or "[X]"
        screen = block.get("en_pantalla") or "[X]"
        lines.append(f"- **{name}:** {text} (en pantalla: {screen})")
    return "\n".join(lines)
