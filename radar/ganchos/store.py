"""Filas de la biblioteca. Una pieza por fuente y URL."""

from __future__ import annotations

import json
from typing import Any

from radar.db import Database, utc_now

_FUENTES = {"tiktok", "instagram"}


def guardar_pieza(db: Database, datos: dict[str, Any]) -> int:
    fuente = str(datos.get("fuente") or "")
    url = str(datos.get("url") or "").strip()
    if fuente not in _FUENTES or not url:
        raise ValueError("La pieza necesita fuente y URL.")
    ahora = utc_now()
    diapositivas = datos.get("diapositivas")
    if diapositivas is None:
        diapositivas_json = datos.get("diapositivas_json") or "[]"
    else:
        diapositivas_json = json.dumps(diapositivas, ensure_ascii=False)
    with db.connect() as conn:
        previo = conn.execute(
            "SELECT id, created_at FROM gh_piezas WHERE fuente = ? AND url = ?",
            (fuente, url),
        ).fetchone()
        columnas = {
            "fuente": fuente,
            "url": url,
            "handle": datos.get("handle") or "",
            "nicho": datos.get("nicho") or "",
            "subnicho": datos.get("subnicho") or "",
            "vistas": datos.get("vistas"),
            "mediana": datos.get("mediana"),
            "multiplo": datos.get("multiplo"),
            "fijada": 1 if datos.get("fijada") else 0,
            "caption": datos.get("caption") or "",
            "num_diapositivas": datos.get("num_diapositivas"),
            "diapositivas_json": diapositivas_json,
            "producto_indice": datos.get("producto_indice"),
            "cta": datos.get("cta") or "",
            "transcripcion": datos.get("transcripcion") or "",
            "texto_pantalla": datos.get("texto_pantalla") or "",
            "gancho_texto": datos.get("gancho_texto") or "",
            "estado": datos.get("estado") or "pendiente",
            "error": datos.get("error") or "",
            "extraida_en": datos.get("extraida_en") or "",
        }
        if previo is None:
            columnas["created_at"] = ahora
            nombres = list(columnas)
            conn.execute(
                f"INSERT INTO gh_piezas ({', '.join(nombres)}) VALUES ({', '.join('?' for _ in nombres)})",
                [columnas[nombre] for nombre in nombres],
            )
            fila = conn.execute("SELECT id FROM gh_piezas WHERE fuente = ? AND url = ?", (fuente, url)).fetchone()
            return int(fila["id"])
        asignacion = ", ".join(f"{nombre} = ?" for nombre in columnas if nombre not in {"fuente", "url"})
        valores = [columnas[nombre] for nombre in columnas if nombre not in {"fuente", "url"}]
        conn.execute(
            f"UPDATE gh_piezas SET {asignacion} WHERE id = ?",
            [*valores, int(previo["id"])],
        )
        return int(previo["id"])


def obtener_por_url(db: Database, fuente: str, url: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        fila = conn.execute(
            "SELECT * FROM gh_piezas WHERE fuente = ? AND url = ?",
            (fuente, url),
        ).fetchone()
    return None if fila is None else _pieza(fila, None)


def guardar_analisis(db: Database, pieza_id: int, datos: dict[str, Any]) -> None:
    ahora = utc_now()
    avisos = datos.get("warnings")
    if avisos is None:
        avisos_json = datos.get("warnings_json") or "[]"
    else:
        avisos_json = json.dumps(list(avisos), ensure_ascii=False)
    columnas = {
        "hook": datos.get("hook") or "",
        "hook_type": datos.get("hook_type") or "",
        "formato": datos.get("formato") or "",
        "estructura": datos.get("estructura") or "",
        "insercion": datos.get("insercion") or "",
        "angulo": datos.get("angulo") or "",
        "por_que": datos.get("por_que") or "",
        "plantilla": datos.get("plantilla") or "",
        "adaptacion": datos.get("adaptacion") or "",
        "warnings_json": avisos_json,
        "provider": datos.get("provider") or "",
        "model": datos.get("model") or "",
        "raw_json": datos.get("raw") or datos.get("raw_json") or "",
        "error": datos.get("error") or "",
        "analizado_en": datos.get("analizado_en") or ("" if datos.get("error") else ahora),
    }
    with db.connect() as conn:
        previo = conn.execute("SELECT pieza_id FROM gh_analisis WHERE pieza_id = ?", (pieza_id,)).fetchone()
        if previo is None:
            nombres = ["pieza_id", *columnas.keys()]
            conn.execute(
                f"INSERT INTO gh_analisis ({', '.join(nombres)}) VALUES ({', '.join('?' for _ in nombres)})",
                [pieza_id, *[columnas[nombre] for nombre in columnas]],
            )
            return
        asignacion = ", ".join(f"{nombre} = ?" for nombre in columnas)
        conn.execute(
            f"UPDATE gh_analisis SET {asignacion} WHERE pieza_id = ?",
            [*columnas.values(), pieza_id],
        )


def borrar_analisis(db: Database, pieza_id: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM gh_analisis WHERE pieza_id = ?", (pieza_id,))


def pendientes_de_analisis(db: Database) -> list[dict[str, Any]]:
    """Extraídas, no fijadas, sin un análisis guardado. Un error se reintenta."""
    consulta = """
        SELECT p.*, a.hook_type, a.formato, a.plantilla, a.adaptacion, a.error AS analisis_error,
               a.hook, a.estructura, a.insercion, a.angulo, a.por_que, a.warnings_json, a.analizado_en
        FROM gh_piezas p
        LEFT JOIN gh_analisis a ON a.pieza_id = p.id
        WHERE p.fijada = 0 AND p.estado = 'extraida' AND (a.pieza_id IS NULL OR a.analizado_en = '')
        ORDER BY COALESCE(p.multiplo, -1) DESC, COALESCE(p.vistas, -1) DESC, p.url
    """
    with db.connect() as conn:
        filas = conn.execute(consulta).fetchall()
    return [_pieza(fila, fila) for fila in filas]


def listar(
    db: Database,
    *,
    fuente: str | None = None,
    nicho: str | None = None,
    subnicho: str | None = None,
    hook_type: str | None = None,
    formato: str | None = None,
    min_multiplo: float | None = None,
    incluir_fijadas: bool = False,
) -> list[dict[str, Any]]:
    consulta = """
        SELECT p.*, a.hook, a.hook_type, a.formato, a.estructura, a.insercion, a.angulo,
               a.por_que, a.plantilla, a.adaptacion, a.warnings_json, a.analizado_en,
               a.error AS analisis_error
        FROM gh_piezas p
        LEFT JOIN gh_analisis a ON a.pieza_id = p.id
        WHERE 1 = 1
    """
    parametros: list[Any] = []
    if not incluir_fijadas:
        consulta += " AND p.fijada = 0 AND p.estado != 'omitida'"
    if fuente and fuente != "todas":
        consulta += " AND p.fuente = ?"
        parametros.append(fuente)
    if nicho:
        consulta += " AND p.nicho = ?"
        parametros.append(nicho)
    if subnicho:
        consulta += " AND p.subnicho = ?"
        parametros.append(subnicho)
    if hook_type:
        consulta += " AND a.hook_type = ?"
        parametros.append(hook_type)
    if formato:
        consulta += " AND a.formato = ?"
        parametros.append(formato)
    if min_multiplo is not None:
        consulta += " AND p.multiplo IS NOT NULL AND p.multiplo >= ?"
        parametros.append(min_multiplo)
    consulta += " ORDER BY COALESCE(p.multiplo, -1) DESC, COALESCE(p.vistas, -1) DESC, p.url"
    with db.connect() as conn:
        filas = conn.execute(consulta, parametros).fetchall()
    return [_pieza(fila, fila) for fila in filas]


def valores(db: Database, columna: str) -> list[str]:
    if columna in {"nicho", "subnicho", "fuente"}:
        consulta = f"SELECT DISTINCT {columna} AS valor FROM gh_piezas WHERE fijada = 0 AND {columna} != '' ORDER BY valor"
    elif columna in {"hook_type", "formato"}:
        consulta = (
            f"SELECT DISTINCT a.{columna} AS valor FROM gh_analisis a "
            "JOIN gh_piezas p ON p.id = a.pieza_id "
            f"WHERE p.fijada = 0 AND a.{columna} != '' ORDER BY valor"
        )
    else:
        raise ValueError(columna)
    with db.connect() as conn:
        filas = conn.execute(consulta).fetchall()
    return [fila["valor"] for fila in filas]


def _pieza(fila, analisis) -> dict[str, Any]:
    data = dict(fila)
    try:
        diapositivas = json.loads(data.get("diapositivas_json") or "[]")
    except json.JSONDecodeError:
        diapositivas = []
    data["diapositivas"] = diapositivas if isinstance(diapositivas, list) else []
    try:
        avisos = json.loads(data.get("warnings_json") or "[]")
    except json.JSONDecodeError:
        avisos = []
    data["warnings"] = avisos if isinstance(avisos, list) else []
    data["fijada"] = bool(data.get("fijada"))
    if analisis is None:
        for clave in (
            "hook",
            "hook_type",
            "formato",
            "estructura",
            "insercion",
            "angulo",
            "por_que",
            "plantilla",
            "adaptacion",
        ):
            data.setdefault(clave, "")
    return data
