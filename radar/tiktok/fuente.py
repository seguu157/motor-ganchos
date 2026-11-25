"""Peticiones a páginas públicas de TikTok, con pausa y parada si bloquea."""

from __future__ import annotations

import random
from dataclasses import dataclass

from radar.tiktok.consultas import enlaces_de_listado
from radar.tiktok.parse import (
    EmbedParseado,
    PerfilParseado,
    handles_publicos,
    parece_bloqueo,
    parsear_embed,
    parsear_perfil,
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class TikTokBloqueado(RuntimeError):
    """TikTok ha cortado la lectura. No se reintenta en la misma pasada."""


# Un 429 se espera una vez, con una pausa larga, y se repite esa petición.
# Si vuelve a responder 429, la pasada para. 403 y 503 paran a la primera.
PAUSA_429 = (60.0, 120.0)


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
    """Embed del tag: el `/tag/` a secas no trae autores sin JavaScript."""
    limpio = "".join(caracter for caracter in tag.casefold() if caracter.isalnum() or caracter == "_")
    return f"https://www.tiktok.com/embed/tag/{limpio}"


class Lector:
    """Una pasada. Un 403 para en seco. Un 429 espera y se reintenta una vez."""

    def __init__(
        self,
        transporte: Transporte,
        *,
        dormir=None,
        rng: random.Random | None = None,
        pausa_429: tuple[float, float] = PAUSA_429,
    ) -> None:
        self.transporte = transporte
        self._vacios = 0
        self._dormir = dormir or _dormir
        self._rng = rng or random.Random()
        self._pausa_429 = pausa_429
        self._reintento_429 = False

    def leer(self, url: str) -> Respuesta:
        return self._leer(url)

    def _leer(self, url: str) -> Respuesta:
        respuesta = self.transporte.get(url)
        if respuesta.status == 429 and not self._reintento_429:
            self._reintento_429 = True
            bajo, alto = self._pausa_429
            if alto > 0:
                self._dormir(self._rng.uniform(bajo, alto))
            respuesta = self.transporte.get(url)
            if respuesta.status == 429:
                raise TikTokBloqueado(
                    "TikTok ha respondido 429. Se ha esperado y se ha reintentado una vez. "
                    "Lo ya guardado se queda. No se reintenta más en esta pasada."
                )
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
        return self.leer_pagina(url)[0]

    def leer_pagina(self, url: str) -> tuple[list[str], list[str]]:
        respuesta = self._leer(url)
        if respuesta.status >= 400 or parece_bloqueo(respuesta.text):
            return [], []
        return handles_publicos(respuesta.text), enlaces_de_listado(respuesta.text, url)


def pausar(rng: random.Random, dormir, tramo: tuple[float, float]) -> None:
    bajo, alto = tramo
    if alto <= 0:
        return
    dormir(rng.uniform(bajo, alto))


def _dormir(segundos: float) -> None:
    import time

    time.sleep(segundos)
