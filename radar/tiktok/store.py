"""Tablas TikTok dentro del SQLite del radar. Una fila por handle."""

from __future__ import annotations

import json
from typing import Any

from radar.db import Database, utc_now
from radar.texto import plegar
from radar.tiktok.reglas import normalizar_handle

COLUMNAS_CSV = (
    "handle",
    "url",
    "nombre",
    "bio",
    "enlace_bio",
    "que_vende",
    "tipo_producto",
    "nicho",
    "subnicho",
    "idioma",
    "seguidores",
    "posts_muestreados",
    "fijados",
    "mediana_vistas",
    "slideshows_muestra",
    "es_cuenta_slideshow",
    "num_piezas_virales",
    "num_virales_no_fijadas",
    "mejor_reciente_url",
    "mejor_reciente_vistas",
    "mejor_reciente_multiplo",
    "mejor_pieza_url",
    "mejor_pieza_vistas",
    "mejor_pieza_multiplo",
    "mejor_pieza_slideshow",
    "mejor_pieza_fijada",
    "piezas_virales",
    "ultima_publicacion",
    "clasificacion",
    "fecha_detectada",
    "fecha_actualizada",
    "fuente",
    "semilla",
)

_ENTEROS = {
    "seguidores",
    "posts_muestreados",
    "fijados",
    "mediana_vistas",
    "num_piezas_virales",
    "num_virales_no_fijadas",
    "mejor_reciente_vistas",
    "mejor_pieza_vistas",
}
_REALES = {"mejor_reciente_multiplo", "mejor_pieza_multiplo"}
_MANUALES = ("que_vende", "tipo_producto", "nicho", "subnicho", "idioma", "clasificacion")

SEMILLAS_DEFECTO: tuple[tuple[str, str], ...] = (
    ("url", "https://gr0w.app/tiktok"),
    ("url", "https://reelsfarm.com/slideshow-ideas"),
    ("url", "https://slidemill.co/examples"),
    ("url", "https://www.airbridge.io/en/ugc-hooks"),
    ("url", "https://gethandler.ai/outliers"),
    ("hashtag", "slideshow"),
    ("hashtag", "apprecommendation"),
    ("keyword", "app slideshow"),
    ("keyword", "study app"),
    ("keyword", "dating app"),
    ("keyword", "skincare amazon"),
)


def unir_semilla(actual: str, nueva: str) -> str:
    partes = [parte for parte in (actual or "").split(";") if parte]
    for trozo in (nueva or "").split(";"):
        limpio = trozo.strip()
        if limpio and limpio not in partes:
            partes.append(limpio)
    return ";".join(partes)


def guardar_cuenta(
    db: Database,
    datos: dict[str, Any],
    *,
    conservar_manual: bool = True,
    fusionar_semilla: bool = True,
) -> str:
    """Crea o actualiza. La fecha de detección no se mueve si ya existía."""
    handle = normalizar_handle(str(datos.get("handle") or ""))
    if not handle:
        raise ValueError("Falta un handle de TikTok válido.")
    ahora = utc_now()
    with db.connect() as conn:
        previo = conn.execute("SELECT * FROM tt_cuentas WHERE handle = ?", (handle,)).fetchone()
        fila = _fila(datos, handle, insercion=previo is None)
        if previo is not None:
            if previo["fecha_detectada"]:
                fila["fecha_detectada"] = previo["fecha_detectada"]
            manual = "revisada a mano" in (previo["clasificacion"] or "")
            entrante = fila.get("clasificacion") or ""
            if conservar_manual and manual and "revisada a mano" not in entrante:
                for campo in _MANUALES:
                    fila[campo] = previo[campo]
            if fusionar_semilla and "semilla" in fila:
                fila["semilla"] = unir_semilla(previo["semilla"], fila.get("semilla") or "")
            columnas = [clave for clave in fila if clave != "handle"]
            if not columnas:
                return "actualizada"
            asignacion = ", ".join(f"{columna} = ?" for columna in columnas)
            conn.execute(
                f"UPDATE tt_cuentas SET {asignacion} WHERE handle = ?",
                [fila[columna] for columna in columnas] + [handle],
            )
            return "actualizada"
        fila.setdefault("fecha_detectada", ahora[:10])
        fila.setdefault("fecha_actualizada", ahora[:10])
        fila["created_at"] = ahora
        columnas = list(fila.keys())
        marcas = ", ".join("?" for _ in columnas)
        conn.execute(
            f"INSERT INTO tt_cuentas ({', '.join(columnas)}) VALUES ({marcas})",
            [fila[columna] for columna in columnas],
        )
        return "nueva"


def _fila(datos: dict[str, Any], handle: str, *, insercion: bool) -> dict[str, Any]:
    fila: dict[str, Any] = {"handle": handle}
    for columna in COLUMNAS_CSV:
        if columna == "handle" or columna not in datos:
            continue
        fila[columna] = _normalizar_valor(columna, datos.get(columna))
    for clave in ("estado", "motivo", "raw_json"):
        if clave not in datos:
            continue
        valor = datos.get(clave)
        if clave == "raw_json" and valor is not None and not isinstance(valor, str):
            valor = json.dumps(valor, ensure_ascii=False)
        fila[clave] = "" if valor is None else valor
    if "captions" in datos:
        fila["captions_json"] = json.dumps(list(datos.get("captions") or []), ensure_ascii=False)
    elif "captions_json" in datos:
        fila["captions_json"] = datos.get("captions_json") or "[]"
    if insercion:
        fila.setdefault("url", f"https://www.tiktok.com/@{handle}")
        fila.setdefault("estado", "candidata")
        fila.setdefault("motivo", "")
        fila.setdefault("captions_json", "[]")
    elif not fila.get("url"):
        fila.pop("url", None)
    return fila


def _normalizar_valor(columna: str, valor: Any) -> Any:
    if columna in _ENTEROS or columna in _REALES:
        if valor is None or (isinstance(valor, str) and not str(valor).strip()):
            return None
        if columna in _ENTEROS:
            return int(float(valor))
        return float(valor)
    if valor is None:
        return ""
    return str(valor)


def obtener_cuenta(db: Database, handle: str) -> dict[str, Any] | None:
    limpio = normalizar_handle(handle)
    if not limpio:
        return None
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM tt_cuentas WHERE handle = ?", (limpio,)).fetchone()
    return None if row is None else _cuenta(row)


def handles_conocidos(db: Database) -> set[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT handle FROM tt_cuentas").fetchall()
    return {row["handle"] for row in rows}


def listar_cuentas(
    db: Database,
    *,
    nicho: str | None = None,
    subnicho: str | None = None,
    estado: str | None = "califica",
    min_vistas: int | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    consulta = "SELECT * FROM tt_cuentas WHERE 1 = 1"
    parametros: list[Any] = []
    if estado and estado != "todas":
        consulta += " AND estado = ?"
        parametros.append(estado)
    if nicho:
        consulta += " AND nicho = ?"
        parametros.append(nicho)
    if subnicho:
        consulta += " AND subnicho = ?"
        parametros.append(subnicho)
    if min_vistas is not None:
        consulta += " AND mejor_pieza_vistas IS NOT NULL AND mejor_pieza_vistas >= ?"
        parametros.append(min_vistas)
    consulta += " ORDER BY nicho, subnicho, COALESCE(mejor_pieza_vistas, -1) DESC, handle"
    with db.connect() as conn:
        rows = conn.execute(consulta, parametros).fetchall()
    cuentas = [_cuenta(row) for row in rows]
    aguja = plegar(q)
    if not aguja:
        return cuentas
    filtradas = []
    for cuenta in cuentas:
        texto = plegar(
            " ".join(
                str(cuenta.get(campo) or "")
                for campo in ("handle", "nombre", "bio", "que_vende", "nicho", "subnicho", "tipo_producto")
            )
        )
        if aguja in texto:
            filtradas.append(cuenta)
    return filtradas


def pendientes(db: Database, estados: tuple[str, ...], limite: int) -> list[dict[str, Any]]:
    if limite < 1 or not estados:
        return []
    marcas = ", ".join("?" for _ in estados)
    consulta = (
        f"SELECT * FROM tt_cuentas WHERE estado IN ({marcas}) "
        "ORDER BY fecha_actualizada, handle LIMIT ?"
    )
    with db.connect() as conn:
        rows = conn.execute(consulta, [*estados, limite]).fetchall()
    return [_cuenta(row) for row in rows]


def valores_distintos(db: Database, columna: str, *, estado: str | None = "califica") -> list[str]:
    if columna not in {"nicho", "subnicho", "estado"}:
        raise ValueError(columna)
    consulta = f"SELECT DISTINCT {columna} AS valor FROM tt_cuentas WHERE {columna} != ''"
    parametros: list[Any] = []
    if estado and estado != "todas" and columna != "estado":
        consulta += " AND estado = ?"
        parametros.append(estado)
    consulta += " ORDER BY valor"
    with db.connect() as conn:
        rows = conn.execute(consulta, parametros).fetchall()
    return [row["valor"] for row in rows]


def contar_por_estado(db: Database) -> dict[str, int]:
    with db.connect() as conn:
        rows = conn.execute("SELECT estado, COUNT(*) AS n FROM tt_cuentas GROUP BY estado").fetchall()
    return {row["estado"]: int(row["n"]) for row in rows}


def anadir_semilla(db: Database, tipo: str, valor: str, *, notas: str = "") -> None:
    tipo_limpio = (tipo or "").strip().casefold()
    if tipo_limpio not in {"handle", "keyword", "hashtag", "url"}:
        raise ValueError("El tipo de semilla tiene que ser handle, keyword, hashtag o url.")
    limpio = (valor or "").strip()
    if tipo_limpio == "handle":
        limpio = normalizar_handle(limpio)
        if not limpio:
            raise ValueError("Ese handle de TikTok no vale.")
    if tipo_limpio == "url" and not limpio.casefold().startswith(("http://", "https://")):
        raise ValueError("La semilla de URL tiene que empezar por http:// o https://.")
    if not limpio:
        raise ValueError("La semilla está vacía.")
    ahora = utc_now()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO tt_semillas(tipo, valor, activa, notas, leida_en, created_at)
            VALUES(?, ?, 1, ?, '', ?)
            ON CONFLICT(tipo, valor) DO UPDATE SET
                activa = 1,
                notas = CASE WHEN excluded.notas != '' THEN excluded.notas ELSE tt_semillas.notas END
            """,
            (tipo_limpio, limpio, notas, ahora),
        )


def listar_semillas(db: Database, *, solo_activas: bool = True) -> list[dict[str, Any]]:
    consulta = "SELECT * FROM tt_semillas"
    if solo_activas:
        consulta += " WHERE activa = 1"
    consulta += " ORDER BY tipo, leida_en, valor"
    with db.connect() as conn:
        rows = conn.execute(consulta).fetchall()
    return [dict(row) for row in rows]


def asegurar_semillas_defecto(db: Database) -> None:
    existentes = {(semilla["tipo"], semilla["valor"]) for semilla in listar_semillas(db, solo_activas=False)}
    for tipo, valor in SEMILLAS_DEFECTO:
        if (tipo, valor) not in existentes:
            anadir_semilla(db, tipo, valor, notas="semilla por defecto")


def marcar_semilla_leida(db: Database, tipo: str, valor: str, cuando: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE tt_semillas SET leida_en = ? WHERE tipo = ? AND valor = ?",
            (cuando, tipo, valor),
        )


def registrar_pasada(
    db: Database,
    *,
    tipo: str,
    started_at: str,
    finished_at: str,
    status: str,
    message: str,
    leidas: int,
    nuevas: int,
    califican: int,
    descartadas: int,
) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO tt_pasadas(
                tipo, started_at, finished_at, status, message, leidas, nuevas, califican, descartadas
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tipo, started_at, finished_at, status, message, leidas, nuevas, califican, descartadas),
        )


def ultima_pasada(db: Database) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM tt_pasadas ORDER BY id DESC LIMIT 1").fetchone()
    return None if row is None else dict(row)


def _cuenta(row) -> dict[str, Any]:
    data = dict(row)
    try:
        captions = json.loads(data.get("captions_json") or "[]")
    except json.JSONDecodeError:
        captions = []
    data["captions"] = captions if isinstance(captions, list) else []
    return data
