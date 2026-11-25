"""Un reel viral leído con la sesión que ya usa el radar de creadores."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from radar.instagram.fuente import InstagramSesionAusente, PlaywrightSesion
from radar.instagram.parse import _fijado, _pie


@dataclass
class ReelCrudo:
    url: str
    caption: str = ""
    video_url: str = ""
    cover_url: str = ""
    texto_pantalla: str = ""
    transcripcion: str = ""
    segmentos: list[dict[str, Any]] = field(default_factory=list)
    fijado: bool = False
    error: str = ""


def codigo_de_url(url: str) -> str:
    partes = [parte for parte in urlparse(url).path.split("/") if parte]
    if "reel" in partes:
        indice = partes.index("reel")
        if indice + 1 < len(partes):
            return partes[indice + 1]
    if partes:
        return partes[-1]
    return ""


def reel_desde_blobs(blobs: list, url: str, html: str = "") -> ReelCrudo:
    codigo = codigo_de_url(url)
    nodo = _buscar(blobs, codigo) if codigo else None
    if nodo is None:
        return ReelCrudo(url=url, error="sin ficha del reel")
    pantalla = "\n".join(_textos_pantalla(nodo))
    return ReelCrudo(
        url=url,
        caption=_pie(nodo),
        video_url=_video(nodo),
        cover_url=_portada(nodo),
        texto_pantalla=pantalla,
        fijado=_fijado(nodo),
        error="",
    )


class PlaywrightLectorReel:
    """Abre cada reel con el perfil de `radar login`. Hay que cerrarlo."""

    def __init__(self, profile: Path) -> None:
        self.profile = Path(profile)
        self._sesion: PlaywrightSesion | None = None

    def leer(self, url: str) -> ReelCrudo:
        if not (self.profile / "perfil.listo").is_file():
            raise InstagramSesionAusente()
        if self._sesion is None:
            self._sesion = PlaywrightSesion(self.profile, headless=True)
        blobs, _texto = self._sesion.leer_url(url)
        return reel_desde_blobs(blobs, url)

    def close(self) -> None:
        if self._sesion is not None:
            self._sesion.close()
            self._sesion = None


def _buscar(blobs: list, codigo: str) -> dict | None:
    encontrado: dict | None = None

    def walk(nodo) -> None:
        nonlocal encontrado
        if encontrado is not None:
            return
        if isinstance(nodo, dict):
            code = str(nodo.get("code") or nodo.get("shortcode") or "")
            if code == codigo and (_video(nodo) or _pie(nodo) or nodo.get("product_type") or nodo.get("media_type")):
                encontrado = nodo
                return
            for valor in nodo.values():
                walk(valor)
        elif isinstance(nodo, list):
            for item in nodo:
                walk(item)

    walk(blobs)
    return encontrado


def _video(nodo: dict) -> str:
    for clave in ("video_url", "videoUrl", "playback_url"):
        valor = nodo.get(clave)
        if isinstance(valor, str) and valor.startswith("https://"):
            return valor
    versiones = nodo.get("video_versions")
    if isinstance(versiones, list) and versiones and isinstance(versiones[0], dict):
        url = versiones[0].get("url")
        if isinstance(url, str) and url.startswith("https://"):
            return url
    return ""


def _portada(nodo: dict) -> str:
    for clave in ("display_url", "thumbnail_url", "thumbnail_src"):
        valor = nodo.get(clave)
        if isinstance(valor, str) and valor.startswith("https://"):
            return valor
    versiones = nodo.get("image_versions2")
    if isinstance(versiones, dict):
        candidatos = versiones.get("candidates")
        if isinstance(candidatos, list) and candidatos and isinstance(candidatos[0], dict):
            url = candidatos[0].get("url")
            if isinstance(url, str) and url.startswith("https://"):
                return url
    return ""


def _textos_pantalla(nodo: dict) -> list[str]:
    textos: list[str] = []
    for clave in ("accessibility_caption", "overlay_text", "sticker_text"):
        valor = nodo.get(clave)
        if isinstance(valor, str):
            _anadir(textos, valor)

    def walk(valor) -> None:
        if isinstance(valor, dict):
            for nombre, hijo in valor.items():
                if nombre in {"text", "texto"} and isinstance(hijo, str):
                    _anadir(textos, hijo)
                else:
                    walk(hijo)
        elif isinstance(valor, list):
            for item in valor:
                walk(item)

    for clave in ("stickers", "story_stickers", "text_overlays"):
        if clave in nodo:
            walk(nodo[clave])
    return textos


def _anadir(salida: list[str], texto: str) -> None:
    limpio = " ".join((texto or "").split())
    if limpio and limpio not in salida:
        salida.append(limpio)
