"""Consultas que sí devuelven handles sin login.

El listado normal de un hashtag (`/tag/`) y varias bibliotecas en JavaScript
no traen cuentas en el HTML. El embed oficial del tag sí trae `authorUniqueId`.
Los nombres de app que ya están en la base se convierten en esos tags y en
búsquedas `site:tiktok.com/photo`.
"""

from __future__ import annotations

import html as html_lib
import re
from collections import defaultdict
from urllib.parse import quote_plus, urljoin, urlparse

from radar.db import Database
from radar.tiktok.reglas import slugs_de_texto
from radar.tiktok.store import anadir_semilla, listar_cuentas, listar_semillas

_RUIDO = {
    "amazon",
    "app",
    "beacons",
    "ebook",
    "enlace",
    "enlaces",
    "gumroad",
    "linktree",
    "producto",
    "shop",
    "shopmy",
    "store",
    "tienda",
}
_NO_SIGUE = (".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2", ".ico", ".gif", ".webp")
_LISTADO = ("tiktok", "slideshow", "example", "outlier", "hook")


def nombre_producto(que_vende: str) -> str:
    bruto = (que_vende or "").split("(")[0].strip().strip("'\"“”‘’")
    bruto = re.sub(r"\s+", " ", bruto)
    return bruto


def slug_de_nombre(nombre: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", (nombre or "").casefold())
    if len(slug) < 4 or slug in _RUIDO or slug.startswith("producto") or slug.startswith("enlace"):
        return ""
    return slug[:40]


def consulta_site(nombre: str) -> str:
    limpio = nombre_producto(nombre)
    if not limpio or slug_de_nombre(limpio) == "":
        return ""
    return f'site:tiktok.com/photo "{limpio}"'


def url_bing(consulta: str) -> str:
    return "https://www.bing.com/search?format=rss&q=" + quote_plus(consulta)


def url_duckduckgo(consulta: str) -> str:
    return "https://html.duckduckgo.com/html/?q=" + quote_plus(consulta)


def preparar_consultas(db: Database) -> int:
    """Añade tags y búsquedas a partir de lo ya guardado. No sale a la red."""
    nuevas = 0
    for cuenta in listar_cuentas(db, estado="califica"):
        nombre = nombre_producto(cuenta.get("que_vende") or "")
        slug = slug_de_nombre(nombre)
        if slug and _alta(db, "hashtag", slug, "producto de la base"):
            nuevas += 1
        consulta = consulta_site(nombre)
        if consulta:
            if _alta(db, "busqueda", url_bing(consulta), nombre):
                nuevas += 1
            if _alta(db, "busqueda", url_duckduckgo(consulta), nombre):
                nuevas += 1
        texto = " ".join([cuenta.get("enlace_bio") or "", cuenta.get("que_vende") or "", cuenta.get("bio") or ""])
        for dominio in slugs_de_texto(texto):
            if _alta(db, "hashtag", dominio, "dominio de la base"):
                nuevas += 1
    return nuevas


def _alta(db: Database, tipo: str, valor: str, notas: str) -> bool:
    antes = {(semilla["tipo"], semilla["valor"]) for semilla in listar_semillas(db, solo_activas=False)}
    if (tipo, valor) in antes:
        return False
    anadir_semilla(db, tipo, valor, notas=notas)
    return True


def ordenar_paginas(semillas: list[dict], max_paginas: int) -> list[dict]:
    """Mezcla tags, búsquedas y listas. Los tags del embed van por delante."""
    if max_paginas <= 0:
        return []
    grupos: dict[str, list[dict]] = defaultdict(list)
    for semilla in semillas:
        grupos[semilla["tipo"]].append(semilla)
    for tipo in grupos:
        grupos[tipo].sort(key=lambda semilla: (semilla.get("leida_en") or "", semilla["valor"]))
    cursor = {tipo: 0 for tipo in ("hashtag", "keyword", "busqueda", "url")}
    # Los embed de tag son los que de verdad traen handles. La búsqueda y las
    # listas entran, pero no se comen la pasada.
    patron = ("hashtag", "hashtag", "hashtag", "hashtag", "keyword", "busqueda", "url")
    elegidas: list[dict] = []
    while len(elegidas) < max_paginas:
        avanzo = False
        for tipo in patron:
            if len(elegidas) >= max_paginas:
                break
            lista = grupos.get(tipo) or []
            indice = cursor[tipo]
            if indice >= len(lista):
                continue
            cursor[tipo] = indice + 1
            elegidas.append(lista[indice])
            avanzo = True
        if not avanzo:
            break
    return elegidas


def enlaces_de_listado(html: str, pagina_url: str) -> list[str]:
    """Otras páginas del mismo sitio que parecen un listado, no estáticos."""
    host = (urlparse(pagina_url).hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    # En tiktok.com el embed ya trae los autores. /tag/ no lista cuentas sin login.
    if not host or host == "tiktok.com" or host.endswith(".tiktok.com"):
        return []
    vistos: list[str] = []
    propia = pagina_url.split("#")[0].rstrip("/")
    for href in re.findall(r'href="([^"]+)"', html or ""):
        absoluto = urljoin(pagina_url, html_lib.unescape(href)).split("#")[0]
        partes = urlparse(absoluto)
        if partes.scheme not in {"http", "https"}:
            continue
        otro = (partes.hostname or "").casefold()
        if otro.startswith("www."):
            otro = otro[4:]
        if otro != host:
            continue
        ruta = (partes.path or "").casefold()
        if any(ruta.endswith(extension) for extension in _NO_SIGUE):
            continue
        if not any(token in ruta for token in _LISTADO):
            continue
        limpio = absoluto.rstrip("/")
        if limpio == propia or limpio in vistos:
            continue
        vistos.append(limpio)
        if len(vistos) >= 20:
            break
    return vistos
