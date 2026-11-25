"""Peticiones a páginas públicas de TikTok, con pausa y parada si bloquea."""

from __future__ import annotations

import random
from dataclasses import dataclass

from radar.tiktok.parse import (
    EmbedParseado,
    PerfilParseado,
    handles_en_rehidratacion,
    parece_bloqueo,
    parsear_embed,
    parsear_perfil,
)
from radar.tiktok.reglas import handles_en_html

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class TikTokBloqueado(RuntimeError):
    """TikTok ha cortado la lectura. No se reintenta en la misma pasada."""


@dataclass
class Respuesta:
    status: int
    text: str
    url: str


class Transporte:
    def get(self, url: str) -> Respuesta:  # pragma: no cover - interfaz
        raise NotImplementedError

    def close(self) -> None:
        return None


class HttpxTransporte(Transporte):
    def __init__(self, timeout: float = 25) -> None:
        import httpx

        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": UA,
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            },
        )

    def get(self, url: str) -> Respuesta:
        import httpx

        try:
            respuesta = self._http.get(url)
        except httpx.HTTPError as exc:
            raise TikTokBloqueado(f"No se pudo leer TikTok ({exc.__class__.__name__}).") from exc
        return Respuesta(respuesta.status_code, respuesta.text, str(respuesta.url))

    def close(self) -> None:
        self._http.close()


def url_perfil(handle: str) -> str:
    return f"https://www.tiktok.com/@{handle}"


def url_embed(handle: str) -> str:
    return f"https://www.tiktok.com/embed/@{handle}"


def url_hashtag(tag: str) -> str:
    limpio = "".join(caracter for caracter in tag.casefold() if caracter.isalnum() or caracter == "_")
    return f"https://www.tiktok.com/tag/{limpio}"


class Lector:
    """Una pasada. Un 403/429 para en seco. Dos páginas vacías seguidas, también."""

    def __init__(self, transporte: Transporte) -> None:
        self.transporte = transporte
        self._vacios = 0

    def _leer(self, url: str) -> Respuesta:
        respuesta = self.transporte.get(url)
        if respuesta.status in {403, 429, 503}:
            raise TikTokBloqueado(
                f"TikTok ha respondido {respuesta.status}. "
                "Lo ya guardado se queda. No se reintenta en esta pasada."
            )
        return respuesta

    def anotar(self, *, vacio: bool) -> None:
        if not vacio:
            self._vacios = 0
            return
        self._vacios += 1
        if self._vacios >= 2:
            raise TikTokBloqueado(
                "TikTok no está devolviendo la página pública. "
                "Lo ya guardado se queda. No se reintenta en esta pasada."
            )

    def perfil(self, handle: str) -> PerfilParseado:
        respuesta = self._leer(url_perfil(handle))
        if respuesta.status >= 400 or parece_bloqueo(respuesta.text):
            return PerfilParseado(handle, "", "", "", None, None, "", False, error=f"http {respuesta.status}")
        return parsear_perfil(respuesta.text, handle)

    def embed(self, handle: str) -> EmbedParseado:
        respuesta = self._leer(url_embed(handle))
        if respuesta.status >= 400 or parece_bloqueo(respuesta.text):
            return EmbedParseado([], error=f"http {respuesta.status}")
        return parsear_embed(respuesta.text, handle)

    def handles_de_pagina(self, url: str) -> list[str]:
        respuesta = self._leer(url)
        if respuesta.status >= 400 or parece_bloqueo(respuesta.text):
            return []
        vistos: list[str] = []
        for handle in [*handles_en_rehidratacion(respuesta.text), *handles_en_html(respuesta.text)]:
            if handle not in vistos:
                vistos.append(handle)
        return vistos


def pausar(rng: random.Random, dormir, tramo: tuple[float, float]) -> None:
    bajo, alto = tramo
    if alto <= 0:
        return
    dormir(rng.uniform(bajo, alto))
