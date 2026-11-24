"""Texto, plantilla y ficheros de un carrusel.

La plantilla deja [X]: el texto leído de la diapositiva es la evidencia, no
el guion de Antonio. Las imágenes de terceros se borran al terminar salvo
que se pida conservarlas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from radar.ocr import OcrNotInstalled

HOSTS_DE_IMAGEN = ("cdninstagram.com", "fbcdn.net", "instagram.com")


def rol_diapositiva(indice: int, total: int) -> str:
    if indice <= 1:
        return "gancho"
    if total > 1 and indice == total:
        return "cierre"
    return "desarrollo"


def plantilla_carrusel(slides: list[dict[str, Any]]) -> str:
    if not slides:
        return "Diapositiva 1 (gancho): [X]\nDiapositiva 2 (cierre): [X]"
    total = len(slides)
    lineas = []
    for slide in slides:
        indice = int(slide.get("indice") or len(lineas) + 1)
        lineas.append(f"Diapositiva {indice} ({rol_diapositiva(indice, total)}): [X]")
    return "\n".join(lineas)


def descargar_imagen(url: str, workdir: Path, indice: int) -> Path | None:
    """Baja una imagen de un CDN de Instagram. Otro host no se toca."""
    if not url_de_imagen_permitida(url):
        return None
    destino = workdir / f"diapositiva-{indice}.img"
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.get(url)
    except httpx.HTTPError:
        return None
    if response.status_code != 200 or not response.content:
        return None
    destino.write_bytes(response.content)
    return destino


def url_de_imagen_permitida(url: str | None) -> bool:
    if not url or not url.startswith("https://"):
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(host == permitido or host.endswith("." + permitido) for permitido in HOSTS_DE_IMAGEN)


def completar_diapositivas(
    slides: list[dict[str, Any]],
    *,
    ocr: Callable[[Path], str],
    downloader: Callable[[str, Path, int], Path | None],
    workdir: Path,
    conservar: bool,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Rellena el texto que falta y borra el fichero descargado.

    Devuelve las diapositivas y las rutas que siguen en disco. Con
    `conservar` falso, esa lista va vacía.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    hechas: list[dict[str, Any]] = []
    conservadas: list[Path] = []
    for posicion, slide in enumerate(slides, start=1):
        indice = int(slide.get("indice") or posicion)
        texto = str(slide.get("texto") or "").strip()
        origen = str(slide.get("origen") or ("texto" if texto else "vacio"))
        imagen = slide.get("imagen") or None
        local: Path | None = None
        if not texto and imagen and url_de_imagen_permitida(str(imagen)):
            local = downloader(str(imagen), workdir, indice)
            if local is not None:
                try:
                    texto = (ocr(local) or "").strip()
                    origen = "ocr" if texto else "sin_texto"
                except OcrNotInstalled:
                    if not conservar:
                        local.unlink(missing_ok=True)
                    raise
                except OSError:
                    texto = ""
                    origen = "sin_texto"
                if conservar:
                    conservadas.append(local)
                else:
                    local.unlink(missing_ok=True)
                    local = None
            else:
                origen = "sin_texto"
        hechas.append(
            {
                "indice": indice,
                "texto": texto,
                "origen": origen if texto or origen == "sin_texto" else ("texto" if texto else "vacio"),
                "imagen": str(local) if conservar and local is not None else None,
            }
        )
    if not conservar:
        for sobra in workdir.glob("*"):
            if sobra.is_file():
                sobra.unlink(missing_ok=True)
        conservadas = []
    return hechas, conservadas


def fusionar_diapositivas(
    guardadas: list[dict[str, Any]],
    nuevas: list[dict[str, Any]],
    *,
    media_kind: str,
) -> list[dict[str, Any]]:
    """Conserva el texto ya leído si la fuente nueva llega vacía.

    Una tanda posterior no debe borrar el OCR. Si el origen trae texto, ese
    texto manda. Si la pieza ya no es un carrusel, no quedan diapositivas.
    """
    if media_kind != "carrusel":
        return []
    if not nuevas:
        return [dict(slide) for slide in guardadas if isinstance(slide, dict)]
    previas: dict[int, dict[str, Any]] = {}
    for posicion, slide in enumerate(guardadas, start=1):
        if isinstance(slide, dict):
            previas[int(slide.get("indice") or posicion)] = slide
    fusion: list[dict[str, Any]] = []
    for posicion, nueva in enumerate(nuevas, start=1):
        if not isinstance(nueva, dict):
            continue
        indice = int(nueva.get("indice") or posicion)
        previa = previas.get(indice) or {}
        texto_nuevo = str(nueva.get("texto") or "").strip()
        texto_previo = str(previa.get("texto") or "").strip()
        if texto_nuevo:
            fusion.append(
                {
                    "indice": indice,
                    "texto": texto_nuevo,
                    "origen": nueva.get("origen") or "texto",
                    "imagen": nueva.get("imagen"),
                }
            )
        elif texto_previo:
            fusion.append(
                {
                    "indice": indice,
                    "texto": texto_previo,
                    "origen": previa.get("origen") or "ocr",
                    "imagen": None,
                }
            )
        else:
            fusion.append(
                {
                    "indice": indice,
                    "texto": "",
                    "origen": nueva.get("origen") or "vacio",
                    "imagen": nueva.get("imagen"),
                }
            )
    return fusion


def texto_unido(slides: list[dict[str, Any]]) -> str:
    partes = []
    for slide in slides:
        texto = str(slide.get("texto") or "").strip()
        if not texto:
            continue
        partes.append(f"Diapositiva {slide.get('indice')}: {texto}")
    return "\n\n".join(partes)
