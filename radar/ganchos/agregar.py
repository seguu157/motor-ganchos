"""Rankings calculados solo con las piezas guardadas."""

from __future__ import annotations

import statistics
from typing import Any


def rankings(piezas: list[dict[str, Any]], campo: str) -> list[dict[str, Any]]:
    """Agrupa por nicho, subnicho y el campo (formato o tipo de gancho)."""
    if campo not in {"formato", "hook_type"}:
        raise ValueError(campo)
    grupos: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for pieza in piezas:
        if pieza.get("fijada"):
            continue
        etiqueta = str(pieza.get(campo) or "").strip()
        if not etiqueta:
            continue
        clave = (str(pieza.get("nicho") or ""), str(pieza.get("subnicho") or ""), etiqueta)
        grupos.setdefault(clave, []).append(pieza)
    filas = []
    for (nicho, subnicho, etiqueta), miembros in grupos.items():
        multiplos = [float(item["multiplo"]) for item in miembros if item.get("multiplo") is not None]
        filas.append(
            {
                "nicho": nicho,
                "subnicho": subnicho,
                "etiqueta": etiqueta,
                "n": len(miembros),
                "mediana_multiplo": float(statistics.median(multiplos)) if multiplos else None,
                "piezas": [
                    {
                        "url": item.get("url") or "",
                        "multiplo": item.get("multiplo"),
                        "vistas": item.get("vistas"),
                        "handle": item.get("handle") or "",
                    }
                    for item in miembros
                ],
            }
        )
    filas.sort(
        key=lambda fila: (
            -fila["n"],
            -(fila["mediana_multiplo"] if fila["mediana_multiplo"] is not None else -1),
            fila["nicho"],
            fila["etiqueta"],
        )
    )
    return filas
