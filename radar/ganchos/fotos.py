"""Texto de un slideshow público de TikTok: oEmbed, embed y JSON de la página."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

from radar.ocr import OcrNotInstalled
from radar.tiktok.fuente import Lector, TikTokBloqueado, pausar
from radar.tiktok.parse import _SCRIPT_EMBED, _SCRIPT_REHIDRATACION

_SCRIPT_JSON = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
    re.S,
)
_HOSTS = ("tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "byteimg.com", "ibyteimg.com", "muscdn.com")
_CTA = re.compile(
    r"link in (?:my |the )?bio|enlace en (?:la |mi )?bio|descarg\w*|download|"
    r"comenta\w*|comment|s[ií]gueme|follow|shop now|compra|app store|play store|"
    r"prueba gratis|link below",
    re.I,
)
_PRODUCTO = re.compile(
    r"\bapp\b|download|descarg\w*|link in (?:my |the )?bio|enlace en (?:la |mi )?bio|"
    r"\bshop\b|tienda|amazon|play store|app store",
    re.I,
)


@dataclass
class LecturaFoto:
    caption: str = ""
    imagenes: list[str] = field(default_factory=list)
    origen: str = ""
    error: str = ""


def id_de_url(url: str) -> str:
    partes = [parte for parte in urlparse(url).path.split("/") if parte]
    if not partes:
        return ""
    return partes[-1].split("?")[0]


def url_oembed(url: str) -> str:
    return "https://www.tiktok.com/oembed?url=" + quote(url, safe="")


def url_embed_pieza(url: str) -> str:
    pieza = id_de_url(url)
    return f"https://www.tiktok.com/embed/v2/{pieza}" if pieza else url


def url_movil(url: str) -> str:
    """La ficha de móvil trae imagePost en el HTML. La de escritorio, no."""
    pieza = id_de_url(url)
    return f"https://m.tiktok.com/v/{pieza}.html" if pieza else url


def fuentes_publicas(url: str) -> list[tuple[str, str]]:
    return [
        (url_movil(url), "movil"),
        (url, "pagina"),
        (url_embed_pieza(url), "embed"),
        (url_oembed(url), "oembed"),
    ]


def host_de_imagen_permitido(url: str) -> bool:
    if not url.startswith("https://"):
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(host == permitido or host.endswith("." + permitido) for permitido in _HOSTS)


def documentos(texto: str) -> list:
    bruto = (texto or "").strip()
    salida: list = []
    if bruto[:1] in "{[":
        try:
            salida.append(json.loads(bruto))
        except json.JSONDecodeError:
            pass
    for patron in (_SCRIPT_REHIDRATACION, _SCRIPT_EMBED, _SCRIPT_JSON):
        for coincidencia in patron.finditer(texto or ""):
            try:
                salida.append(json.loads(coincidencia.group(1)))
            except json.JSONDecodeError:
                continue
    return salida


def imagenes_de_documento(data) -> list[str]:
    encontradas: list[str] = []

    def walk(nodo) -> None:
        if isinstance(nodo, dict):
            for clave in ("imagePost", "image_post"):
                if clave in nodo:
                    _imagenes_de_bloque(nodo.get(clave), encontradas)
            for valor in nodo.values():
                walk(valor)
        elif isinstance(nodo, list):
            for item in nodo:
                walk(item)

    walk(data)
    return _unicas(encontradas)


def caption_de_documento(data) -> str:
    encontrado = ""

    def walk(nodo) -> None:
        nonlocal encontrado
        if encontrado:
            return
        if isinstance(nodo, list):
            for item in nodo:
                walk(item)
            return
        if not isinstance(nodo, dict):
            return
        desc = nodo.get("desc")
        if isinstance(desc, str) and desc.strip():
            encontrado = desc.strip()
            return
        titulo = nodo.get("title")
        if isinstance(titulo, str) and titulo.strip() and ("author_name" in nodo or "imagePost" in nodo):
            encontrado = titulo.strip()
            return
        for valor in nodo.values():
            if encontrado:
                return
            walk(valor)

    walk(data)
    return encontrado


def miniatura_oembed(data) -> str:
    if isinstance(data, dict):
        url = str(data.get("thumbnail_url") or "")
        if host_de_imagen_permitido(url):
            return url
    return ""


def incorporar_html(lectura: LecturaFoto, html: str, origen: str) -> None:
    for doc in documentos(html):
        if not lectura.caption:
            texto = caption_de_documento(doc)
            if texto:
                lectura.caption = texto
        if not lectura.imagenes:
            imagenes = [item for item in imagenes_de_documento(doc) if host_de_imagen_permitido(item)]
            if imagenes:
                lectura.imagenes = imagenes
                lectura.origen = origen
                continue
            mini = miniatura_oembed(doc)
            if mini and not lectura.imagenes:
                lectura.imagenes = [mini]
                lectura.origen = "miniatura"


def html_con_navegador(url: str) -> str:
    """Último intento: la página pública ya renderizada. Sin Playwright, vacío."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(2_000)
                return page.content() or ""
            finally:
                browser.close()
    except Exception:
        return ""


def leer_slideshow(
    lector: Lector,
    url: str,
    *,
    pausa: tuple[float, float],
    dormir,
    rng,
    navegador=None,
) -> LecturaFoto:
    """Prueba la ficha móvil, la página, el embed y el oEmbed. Para en 403 o 429."""
    lectura = LecturaFoto()
    paginas = fuentes_publicas(url)
    vistos: set[str] = set()
    for indice, (direccion, origen) in enumerate(paginas):
        if direccion in vistos:
            continue
        vistos.add(direccion)
        if indice:
            pausar(rng, dormir, pausa)
        try:
            respuesta = lector.leer(direccion)
        except TikTokBloqueado:
            raise
        if respuesta.status >= 400:
            lectura.error = lectura.error or f"http {respuesta.status}"
            continue
        incorporar_html(lectura, respuesta.text, origen)
        if lectura.imagenes and lectura.origen != "miniatura" and lectura.caption:
            break
    if not lectura.imagenes and navegador is not None:
        html = navegador(url) or ""
        if html:
            incorporar_html(lectura, html, "navegador")
    if not lectura.imagenes:
        lectura.error = lectura.error or "sin imágenes públicas"
    return lectura


def aplicar_ocr(
    lectura: LecturaFoto,
    *,
    ocr,
    descargar,
    workdir: Path,
    pistas: list[str],
) -> tuple[list[dict], int | None, str, str]:
    """Devuelve diapositivas, índice de producto, CTA y un aviso si el OCR falla."""
    workdir.mkdir(parents=True, exist_ok=True)
    diapositivas: list[dict] = []
    aviso = ""
    for indice, imagen in enumerate(lectura.imagenes, start=1):
        texto = ""
        origen = lectura.origen or "ocr"
        if host_de_imagen_permitido(imagen):
            local = descargar(imagen, workdir, indice)
            if local is None:
                origen = "sin_imagen"
            else:
                try:
                    texto = (ocr(local) or "").strip()
                    origen = "ocr" if texto else "sin_texto"
                except OcrNotInstalled:
                    raise
                except OSError:
                    texto = ""
                    origen = "sin_texto"
                local.unlink(missing_ok=True)
        else:
            origen = "sin_imagen"
        diapositivas.append({"indice": indice, "texto": texto, "origen": origen})
    producto = indice_de_producto(diapositivas, pistas)
    cta = texto_cta(diapositivas, lectura.caption)
    if lectura.imagenes and not any(item["texto"] for item in diapositivas):
        aviso = "El OCR no leyó texto en las diapositivas. No se inventa el gancho."
    return diapositivas, producto, cta, aviso


def indice_de_producto(diapositivas: list[dict], pistas: list[str]) -> int | None:
    agujas = [pista.casefold() for pista in pistas if pista and len(pista) >= 4]
    for slide in diapositivas:
        texto = str(slide.get("texto") or "")
        if not texto:
            continue
        plegado = texto.casefold()
        if _PRODUCTO.search(texto) or any(aguja in plegado for aguja in agujas):
            return int(slide.get("indice") or 0) or None
    return None


def texto_cta(diapositivas: list[dict], caption: str) -> str:
    for slide in reversed(diapositivas):
        texto = str(slide.get("texto") or "").strip()
        if texto and _CTA.search(texto):
            return texto
    if caption and _CTA.search(caption):
        return caption.strip()
    return ""


def pistas_de_producto(que_vende: str, enlace: str) -> list[str]:
    pistas: list[str] = []
    for palabra in re.findall(r"[A-Za-z0-9][A-Za-z0-9 ._-]{2,}", que_vende or ""):
        limpia = palabra.strip(" ._-")
        if len(limpia) >= 4 and limpia.casefold() not in {"app", "tienda", "ebook"}:
            pistas.append(limpia)
    host = (urlparse(enlace if "://" in (enlace or "") else "https://" + enlace).hostname or "") if enlace else ""
    host = host.removeprefix("www.").split(".")[0]
    if len(host) >= 4:
        pistas.append(host)
    return pistas


def _imagenes_de_bloque(bloque, encontradas: list[str]) -> None:
    if not isinstance(bloque, dict):
        return
    imagenes = bloque.get("images")
    if not isinstance(imagenes, list):
        return
    for imagen in imagenes:
        url = _url_de_imagen(imagen)
        if url:
            encontradas.append(url)


def _url_de_imagen(imagen) -> str:
    if isinstance(imagen, str):
        return imagen if imagen.startswith("https://") else ""
    if not isinstance(imagen, dict):
        return ""
    for clave in ("imageURL", "imageUrl", "displayImage", "urlList"):
        valor = imagen.get(clave)
        if isinstance(valor, dict):
            lista = valor.get("urlList") or valor.get("url_list")
            if isinstance(lista, list) and lista:
                return str(lista[0])
        if isinstance(valor, list) and valor:
            return str(valor[0])
        if isinstance(valor, str) and valor.startswith("https://"):
            return valor
    directo = imagen.get("url")
    if isinstance(directo, str) and directo.startswith("https://"):
        return directo
    return ""


def _unicas(urls: list[str]) -> list[str]:
    vistas: list[str] = []
    for url in urls:
        if url and url not in vistas:
            vistas.append(url)
    return vistas
