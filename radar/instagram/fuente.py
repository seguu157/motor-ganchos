"""Pestaña Reels con la sesión de Chromium del motor.

Headless, una ficha cada vez. Un 401, un 403, un 429, un checkpoint, un
challenge o un muro de login paran la pasada entera. No se reintenta.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from radar.instagram.parse import Lectura, handles_en_html, parsear_blobs
from radar.instagram.reglas import normalizar_handle, slugs_de_keyword
from radar.sources.playwright_source import PlaywrightNotInstalled, classify_page

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class InstagramBloqueado(RuntimeError):
    """Instagram ha cortado la lectura. No se reintenta en la misma pasada."""


class InstagramSesionAusente(RuntimeError):
    """No hay sesión del navegador. No se ha abierto Instagram."""

    def __init__(self) -> None:
        super().__init__(
            "Falta la sesión del navegador. Ejecuta `radar login` una vez "
            "e inicia sesión a mano. No se ha abierto Instagram."
        )


def codigo_de_pagina(*, status: int | None, url: str, text: str, statuses: list[int] | None = None) -> str | None:
    """None si se puede leer. `privada` si solo se omite esa cuenta."""
    vistos = set(statuses or [])
    if status is not None:
        vistos.add(status)
    # 401 y 429 en cualquier respuesta de la ficha paran. Un 403 suelto de
    # un endpoint secundario no: solo el de la propia página.
    if 429 in vistos:
        return "429"
    if 401 in vistos:
        return "401"
    if status == 403:
        return "403"
    return classify_page(status=status, url=url, text=text)


def mensaje_parada(codigo: str) -> str:
    textos = {
        "401": "Instagram respondió 401 y la sesión no sirve.",
        "403": "Instagram respondió 403.",
        "429": "Instagram respondió 429 (demasiadas peticiones).",
        "checkpoint": "Instagram pidió un checkpoint.",
        "challenge": "Instagram pidió un challenge.",
        "login": "La sesión del navegador ha caducado.",
    }
    texto = textos.get(codigo, "Instagram pidió parar.")
    return (
        f"{texto} Paro toda la pasada y no reintento. "
        "Lo ya guardado se queda. Si hace falta, entra a mano con `radar login`."
    )


def url_hashtag(tag: str) -> str:
    limpio = "".join(caracter for caracter in tag.casefold() if caracter.isalnum() or caracter == "_")
    return f"https://www.instagram.com/explore/tags/{limpio}/"


def url_de_semilla(tipo: str, valor: str) -> str | None:
    if tipo == "url":
        return valor
    if tipo == "hashtag":
        return url_hashtag(valor)
    if tipo == "keyword":
        slugs = slugs_de_keyword(valor)
        if not slugs:
            return None
        # La palabra suelta («ganar») no es el nicho. Si el conjunto lleva ia/ai, se usa ese slug.
        elegido = next((slug for slug in slugs if "ia" in slug or "ai" in slug), slugs[-1])
        return url_hashtag(elegido)
    return None


def es_instagram(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    return host == "instagram.com" or host.endswith(".instagram.com")


@dataclass
class Sesion:
    """Interfaz que los tests sustituyen. No abre el navegador."""

    def leer_perfil(self, handle: str) -> Lectura:  # pragma: no cover - interfaz
        raise NotImplementedError

    def handles_de_pagina(self, url: str) -> list[str]:  # pragma: no cover - interfaz
        raise NotImplementedError

    def close(self) -> None:
        return None


class PlaywrightSesion(Sesion):
    """Contexto persistente del motor, en headless. Pausas cortas solo dentro de la ficha."""

    def __init__(self, profile_dir: Path, *, headless: bool = True) -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self._playwright: Any = None
        self._context: Any = None

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def leer_perfil(self, handle: str) -> Lectura:
        blobs, status, url, text, statuses = self._abrir(f"https://www.instagram.com/{handle}/reels/", scrolls=3)
        codigo = codigo_de_pagina(status=status, url=url, text=text, statuses=statuses)
        if codigo and codigo != "privada":
            raise InstagramBloqueado(mensaje_parada(codigo))
        lectura = parsear_blobs(blobs, handle)
        if codigo == "privada":
            lectura.privada = True
            lectura.error = "cuenta privada"
        elif not lectura.reels and not lectura.nombre and lectura.seguidores is None and not lectura.bio:
            lectura.error = "sin ficha"
        return lectura

    def handles_de_pagina(self, url: str) -> list[str]:
        if not es_instagram(url):
            return _handles_publicos(_leer_publico(url))
        blobs, status, final, text, statuses = self._abrir(url, scrolls=2)
        codigo = codigo_de_pagina(status=status, url=final, text=text, statuses=statuses)
        if codigo and codigo != "privada":
            raise InstagramBloqueado(mensaje_parada(codigo))
        if codigo == "privada":
            return []
        vistos: list[str] = []
        for handle in [*_handles_en_blobs(blobs), *handles_en_html(text)]:
            if handle not in vistos:
                vistos.append(handle)
        return vistos

    def _abrir(self, url: str, *, scrolls: int) -> tuple[list, int | None, str, str, list[int]]:
        self._ensure()
        page = self._context.new_page()
        blobs: list = []
        statuses: list[int] = []

        def on_response(response) -> None:
            direccion = response.url or ""
            if "instagram.com" not in direccion:
                return
            if "graphql" not in direccion and "/api/v1/" not in direccion:
                return
            try:
                statuses.append(int(response.status))
            except (TypeError, ValueError):
                return
            try:
                blobs.append(response.json())
            except Exception:  # noqa: BLE001 - un cuerpo que no es JSON no aporta
                return

        page.on("response", on_response)
        try:
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            except Exception as exc:  # noqa: BLE001 - el navegador falla de muchas formas
                raise InstagramBloqueado(
                    "La página no respondió. Paro toda la pasada y no reintento. "
                    f"({type(exc).__name__})"
                ) from exc
            _esperar(page, random.uniform(4_000, 6_000))
            for _ in range(scrolls):
                try:
                    page.mouse.wheel(0, 2200)
                except Exception:  # noqa: BLE001
                    break
                _esperar(page, random.uniform(1_500, 3_000))
            try:
                scripts = page.eval_on_selector_all(
                    "script[type='application/json']",
                    "els => els.map(e => e.textContent || '')",
                )
            except Exception:  # noqa: BLE001
                scripts = []
            for texto in scripts or []:
                bruto = (texto or "").strip()
                if not bruto or bruto[0] not in "{[":
                    continue
                try:
                    blobs.append(json.loads(bruto))
                except json.JSONDecodeError:
                    continue
            try:
                cuerpo = page.inner_text("body")[:4000]
            except Exception:  # noqa: BLE001
                cuerpo = ""
            status = response.status if response is not None else None
            final = page.url or url
            return blobs, status, final, cuerpo, statuses
        finally:
            page.close()

    def _ensure(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PlaywrightNotInstalled() from exc
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=self.headless,
            locale="es-ES",
            viewport={"width": 1280, "height": 900},
        )


def abrir_sesion(settings) -> PlaywrightSesion:
    if not settings.browser_ready:
        raise InstagramSesionAusente()
    return PlaywrightSesion(settings.browser_profile, headless=True)


def pausar(rng: random.Random, dormir, tramo: tuple[float, float]) -> None:
    bajo, alto = tramo
    if alto <= 0:
        return
    dormir(rng.uniform(bajo, alto))


def _esperar(page, milisegundos: float) -> None:
    page.wait_for_timeout(milisegundos)


def _leer_publico(url: str) -> str:
    """Una lista pública, sin cookies de Instagram. Un fallo no para la pasada."""
    import httpx

    try:
        respuesta = httpx.get(
            url,
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"},
        )
    except httpx.HTTPError:
        return ""
    if respuesta.status_code >= 400:
        return ""
    return respuesta.text


def _handles_publicos(html: str) -> list[str]:
    return handles_en_html(html)


def _handles_en_blobs(blobs: list) -> list[str]:
    vistos: list[str] = []
    viewers: set[str] = set()

    def visitar(nodo) -> None:
        if isinstance(nodo, dict):
            for clave, valor in nodo.items():
                if clave in {"viewer", "logged_in_user"} and isinstance(valor, dict):
                    handle = normalizar_handle(str(valor.get("username") or ""))
                    if handle:
                        viewers.add(handle)
                    continue
                if isinstance(valor, dict) and clave in {"user", "owner"}:
                    handle = normalizar_handle(str(valor.get("username") or ""))
                    if handle and handle not in vistos:
                        vistos.append(handle)
                visitar(valor)
        elif isinstance(nodo, list):
            for item in nodo:
                visitar(item)

    for blob in blobs:
        visitar(blob)
    return [handle for handle in vistos if handle not in viewers]
