"""CSV con las mismas columnas que el corte de creadores de Instagram."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from radar.db import Database
from radar.instagram.store import COLUMNAS_CSV, guardar_cuenta, listar_cuentas

SEMILLA_CSV = Path(__file__).resolve().parent.parent / "fixtures" / "cuentas-instagram.csv"
META_SEMILLA = "ig_csv_v1"


def importar_csv(db: Database, path: str | Path) -> int:
    ruta = Path(path)
    with ruta.open(encoding="utf-8-sig", newline="") as fichero:
        lector = csv.DictReader(fichero)
        if lector.fieldnames is None:
            raise ValueError("El CSV no tiene cabecera.")
        faltan = [columna for columna in COLUMNAS_CSV if columna not in lector.fieldnames]
        if faltan:
            raise ValueError("Al CSV le faltan columnas: " + ", ".join(faltan))
        guardadas = 0
        for fila in lector:
            handle = (fila.get("handle") or "").strip()
            if not handle:
                continue
            datos = {columna: fila.get(columna, "") for columna in COLUMNAS_CSV}
            datos["estado"] = "califica"
            datos["motivo"] = ""
            guardar_cuenta(db, datos, conservar_manual=False, fusionar_semilla=True)
            guardadas += 1
    return guardadas


def importar_semilla_si_falta(db: Database) -> int:
    if db.get_meta(META_SEMILLA) == "1":
        return 0
    if not SEMILLA_CSV.is_file():
        return 0
    total = importar_csv(db, SEMILLA_CSV)
    db.set_meta(META_SEMILLA, "1")
    return total


def exportar_csv(
    db: Database,
    path: str | Path | None = None,
    *,
    estado: str | None = "califica",
) -> str:
    texto = _texto(listar_cuentas(db, estado=estado))
    if path is not None and str(path) != "-":
        destino = Path(path)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8", newline="")
    return texto


def _texto(cuentas: list[dict]) -> str:
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNAS_CSV), lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for cuenta in cuentas:
        writer.writerow(_fila_csv(cuenta))
    return buffer.getvalue()


def _fila_csv(cuenta: dict) -> dict[str, str]:
    fila: dict[str, str] = {}
    for columna in COLUMNAS_CSV:
        valor = cuenta.get(columna)
        if valor is None or valor == "":
            fila[columna] = ""
        elif columna == "mejor_pieza_multiplo":
            fila[columna] = f"{float(valor):.1f}"
        else:
            fila[columna] = str(valor)
    return fila
