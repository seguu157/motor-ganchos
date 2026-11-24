"""OCR local para diapositivas sin texto.

`rapidocr-onnxruntime` no necesita Tesseract ni una clave. Los tests inyectan
un motor y no cargan el paquete.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable


class OcrNotInstalled(RuntimeError):
    """Falta el paquete de OCR. No se ha leído ninguna imagen."""


def leer_imagen(path: Path, *, motor: Callable[[Path], str] | None = None) -> str:
    if motor is not None:
        return (motor(path) or "").strip()
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise OcrNotInstalled(
            "Falta el OCR local. Con el entorno activo: pip install rapidocr-onnxruntime. "
            "No hace falta instalar Tesseract. No se ha leído la imagen."
        ) from exc
    engine = RapidOCR()
    result, _elapsed = engine(str(path))
    if not result:
        return ""
    lineas: list[str] = []
    for fila in result:
        if isinstance(fila, (list, tuple)) and len(fila) >= 2 and isinstance(fila[1], str):
            texto = fila[1].strip()
            if texto:
                lineas.append(texto)
    return "\n".join(lineas).strip()
