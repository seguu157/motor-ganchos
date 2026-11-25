"""Tarea diaria en el Programador de tareas de Windows.

El script solo se registra con --instalar, y solo en Windows. En cualquier
sistema se puede imprimir el comando y el .ps1 para copiarlos.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_TAREA = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


@dataclass
class Programa:
    tarea: str
    hora: str
    script: str
    ruta_script: Path
    comando: str


def preparar_tarea(
    *,
    python: str | None = None,
    cwd: str | Path | None = None,
    hora: str = "07:30",
    tarea: str = "RadarCreadoresInstagram",
    destino: str | Path | None = None,
) -> Programa:
    if not _HORA.fullmatch(hora):
        raise ValueError("La hora tiene que ser HH:MM, por ejemplo 07:30.")
    if not _TAREA.fullmatch(tarea):
        raise ValueError("El nombre de la tarea solo puede llevar letras, números, punto, _ y -.")
    interprete = python or sys.executable
    carpeta = Path(cwd or Path.cwd()).resolve()
    ruta = Path(destino) if destino else carpeta / "data" / "radar-creadores-instagram.ps1"
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"Set-Location -LiteralPath {_ps(str(carpeta))}",
            f"& {_ps(interprete)} -m radar creadores ciclo --yes",
            "exit $LASTEXITCODE",
            "",
        ]
    )
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(script, encoding="utf-8", newline="\n")
    archivo = str(ruta)
    comando = (
        "schtasks /Create /F "
        f"/TN {tarea} /SC DAILY /ST {hora} "
        f'/TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \\"{archivo}\\""'
    )
    return Programa(tarea=tarea, hora=hora, script=script, ruta_script=ruta, comando=comando)


def instalar_tarea(programa: Programa) -> str:
    """Registra la tarea. Solo Windows: en otros sistemas no llama a schtasks."""
    import os
    import subprocess

    if os.name != "nt":
        return (
            "El Programador de tareas es de Windows. "
            "El script ya está escrito; el comando para registrarlo es el de arriba."
        )
    resultado = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/F",
            "/TN",
            programa.tarea,
            "/SC",
            "DAILY",
            "/ST",
            programa.hora,
            "/TR",
            f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{programa.ruta_script}"',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if resultado.returncode != 0:
        detalle = (resultado.stderr or resultado.stdout or "").strip()
        raise RuntimeError(detalle or "schtasks no ha podido crear la tarea.")
    return (resultado.stdout or "Tarea registrada.").strip()


def _ps(valor: str) -> str:
    return "'" + valor.replace("'", "''") + "'"
