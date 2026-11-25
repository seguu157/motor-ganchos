"""Markdown y CSV de la biblioteca. Las cifras son las guardadas."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from radar.db import Database
from radar.format_es import format_int_es, format_multiple
from radar.ganchos.agregar import rankings
from radar.ganchos.store import listar

COLUMNAS = (
    "fuente",
    "url",
    "handle",
    "nicho",
    "subnicho",
    "vistas",
    "mediana",
    "multiplo",
    "gancho_texto",
    "hook",
    "hook_type",
    "formato",
    "estructura",
    "insercion",
    "angulo",
    "por_que",
    "plantilla",
    "adaptacion",
    "num_diapositivas",
    "producto_indice",
    "cta",
    "caption",
)


def render_markdown(piezas: list[dict]) -> str:
    lineas = [
        "# Biblioteca de ganchos",
        "",
        "Las cifras salen de las piezas guardadas. La adaptación es una sugerencia, no una métrica.",
        "",
        "## Formatos",
        "",
    ]
    lineas.extend(_ranking(rankings(piezas, "formato")))
    lineas.extend(["", "## Tipos de gancho", ""])
    lineas.extend(_ranking(rankings(piezas, "hook_type")))
    lineas.extend(["", "## Piezas", ""])
    if not piezas:
        lineas.append("No hay piezas extraídas.")
    for pieza in piezas:
        multiplo = _multiplo(pieza.get("multiplo"))
        lineas.append(f"## {multiplo} · @{pieza.get('handle') or '—'} · {pieza.get('fuente')}")
        lineas.append("")
        lineas.append(f"- URL: {pieza.get('url')}")
        lineas.append(f"- Nicho: {pieza.get('nicho') or '—'} · {pieza.get('subnicho') or '—'}")
        lineas.append(
            f"- Vistas: {_entero(pieza.get('vistas'))} · mediana: {_entero(pieza.get('mediana'))} · múltiplo: {multiplo}"
        )
        lineas.append(f"- Gancho leído: {pieza.get('gancho_texto') or '—'}")
        lineas.append(f"- Tipo: {pieza.get('hook_type') or '—'} · formato: {pieza.get('formato') or '—'}")
        if pieza.get("num_diapositivas") is not None:
            producto = pieza.get("producto_indice")
            lineas.append(
                f"- Diapositivas: {pieza.get('num_diapositivas')} · producto en: {producto if producto else '—'}"
            )
        if pieza.get("cta"):
            lineas.append(f"- CTA leído: {pieza['cta']}")
        if pieza.get("plantilla"):
            lineas.extend(["", "### Plantilla", "", pieza["plantilla"], ""])
        if pieza.get("adaptacion"):
            lineas.extend(["### Sugerencia", "", pieza["adaptacion"], ""])
        else:
            lineas.append("")
    return "\n".join(lineas).rstrip() + "\n"


def render_csv(piezas: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNAS, lineterminator="\n")
    writer.writeheader()
    for pieza in piezas:
        writer.writerow({columna: "" if pieza.get(columna) is None else pieza.get(columna) for columna in COLUMNAS})
    return buffer.getvalue()


def exportar(db: Database, destino: str) -> tuple[Path | None, Path | None]:
    piezas = listar(db, incluir_fijadas=False)
    markdown = render_markdown(piezas)
    tabla = render_csv(piezas)
    if destino == "-":
        return None, None
    ruta = Path(destino)
    if ruta.suffix.lower() == ".md":
        md, csv_path = ruta, ruta.with_suffix(".csv")
    elif ruta.suffix.lower() == ".csv":
        csv_path, md = ruta, ruta.with_suffix(".md")
    else:
        ruta.mkdir(parents=True, exist_ok=True)
        md, csv_path = ruta / "ganchos.md", ruta / "ganchos.csv"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(markdown, encoding="utf-8")
    csv_path.write_text(tabla, encoding="utf-8")
    return md, csv_path


def _ranking(filas: list[dict]) -> list[str]:
    if not filas:
        return ["No hay piezas analizadas para este ranking."]
    lineas = []
    for fila in filas:
        nicho = fila["nicho"] or "Sin nicho"
        sub = f" · {fila['subnicho']}" if fila["subnicho"] else ""
        mediana = "—" if fila["mediana_multiplo"] is None else format_multiple(fila["mediana_multiplo"])
        lineas.append(f"- **{fila['etiqueta']}** ({nicho}{sub}): {fila['n']} piezas, mediana {mediana}")
        for pieza in fila["piezas"]:
            multiplo = _multiplo(pieza.get("multiplo"))
            lineas.append(f"  - {multiplo} · {pieza['url']}")
    return lineas


def _multiplo(valor) -> str:
    if valor is None:
        return "—"
    return format_multiple(float(valor))


def _entero(valor) -> str:
    return format_int_es(valor)
