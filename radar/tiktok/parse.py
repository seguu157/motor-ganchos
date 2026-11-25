"""Lectura del HTML público de TikTok. No completa huecos."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from radar.tiktok.reglas import Pieza, entero_vistas, normalizar_handle

_SCRIPT_REHIDRATACION = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.S,
)
_SCRIPT_EMBED = re.compile(
    r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>',
    re.S,
)


@dataclass
class PerfilParseado:
    handle: str
    nombre: str
    bio: str
    enlace: str
    seguidores: int | None
    videos: int | None
    idioma: str
    privada: bool
    error: str = ""
    crudo: dict = field(default_factory=dict)


@dataclass
class EmbedParseado:
    piezas: list[Pieza]
    error: str = ""
    crudo: dict = field(default_factory=dict)


def _json_script(html: str, patron: re.Pattern[str]) -> dict | None:
    match = patron.search(html or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parsear_perfil(html: str, handle: str) -> PerfilParseado:
    data = _json_script(html, _SCRIPT_REHIDRATACION)
    if data is None:
        return PerfilParseado(handle, "", "", "", None, None, "", False, error="sin_rehidratacion")
    scope = data.get("__DEFAULT_SCOPE__")
    detalle = scope.get("webapp.user-detail") if isinstance(scope, dict) else None
    if not isinstance(detalle, dict):
        return PerfilParseado(handle, "", "", "", None, None, "", False, error="sin_user_detail")
    info = detalle.get("userInfo")
    if not isinstance(info, dict):
        codigo = detalle.get("statusCode")
        mensaje = str(detalle.get("statusMsg") or "")
        return PerfilParseado(
            handle,
            "",
            "",
            "",
            None,
            None,
            "",
            False,
            error=f"status {codigo} {mensaje}".strip(),
            crudo={"statusCode": codigo, "statusMsg": mensaje},
        )
    user = info.get("user") if isinstance(info.get("user"), dict) else {}
    stats = info.get("statsV2") if isinstance(info.get("statsV2"), dict) else None
    if stats is None and isinstance(info.get("stats"), dict):
        stats = info.get("stats")
    stats = stats or {}
    bio_link = user.get("bioLink") if isinstance(user.get("bioLink"), dict) else {}
    leido = (user.get("uniqueId") or handle or "").strip()
    return PerfilParseado(
        handle=normalizar_handle(leido) or handle,
        nombre=str(user.get("nickname") or ""),
        bio=str(user.get("signature") or ""),
        enlace=str(bio_link.get("link") or ""),
        seguidores=entero_vistas(stats.get("followerCount")),
        videos=entero_vistas(stats.get("videoCount")),
        idioma=str(user.get("language") or ""),
        privada=bool(user.get("privateAccount")),
        crudo={
            "uniqueId": user.get("uniqueId"),
            "nickname": user.get("nickname"),
            "signature": user.get("signature"),
            "bioLink": bio_link.get("link"),
            "followerCount": stats.get("followerCount"),
            "videoCount": stats.get("videoCount"),
            "language": user.get("language"),
            "privateAccount": user.get("privateAccount"),
        },
    )


def _fecha_de_id(item_id: str) -> str | None:
    if not item_id.isdigit():
        return None
    segundos = int(item_id) >> 32
    if segundos < 1_000_000_000 or segundos > 4_000_000_000:
        return None
    return time.strftime("%Y-%m-%d", time.gmtime(segundos))


def _es_slideshow(cover: str, explicito) -> bool:
    if isinstance(explicito, bool):
        return explicito
    return "photomode" in (cover or "").casefold()


def parsear_embed(html: str, handle: str) -> EmbedParseado:
    data = _json_script(html, _SCRIPT_EMBED)
    if data is None:
        return EmbedParseado([], error="sin_embed")
    source = data.get("source")
    tabla = source.get("data") if isinstance(source, dict) else None
    if not isinstance(tabla, dict):
        return EmbedParseado([], error="sin_embed")
    claves = [clave for clave in tabla if str(clave).startswith("/embed/@")]
    if not claves:
        return EmbedParseado([], error="sin_lista")
    bloque = tabla[claves[0]]
    if not isinstance(bloque, dict):
        return EmbedParseado([], error="sin_lista")
    lista = bloque.get("videoList")
    if not isinstance(lista, list):
        return EmbedParseado([], error="sin_lista", crudo={"claves": claves})
    piezas: list[Pieza] = []
    for item in lista:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        cover = str(item.get("coverUrl") or "")
        slideshow = _es_slideshow(cover, item.get("slideshow") if "slideshow" in item else None)
        image_post = item.get("imagePost")
        if image_post is True or isinstance(image_post, dict):
            slideshow = True
        fijada = bool(item.get("isPinned") or item.get("pinned"))
        desc = str(item.get("desc") or "")
        plays = entero_vistas(item.get("playCount"))
        ruta = "photo" if slideshow else "video"
        piezas.append(
            Pieza(
                item_id=item_id,
                url=f"https://www.tiktok.com/@{handle}/{ruta}/{item_id}",
                desc=desc,
                plays=plays,
                slideshow=slideshow,
                created=_fecha_de_id(item_id),
                fijada=fijada,
            )
        )
    return EmbedParseado(piezas, crudo={"n": len(piezas)})


def handles_en_rehidratacion(html: str) -> list[str]:
    """Autores que aparecen como author o user en el JSON público."""
    data = _json_script(html, _SCRIPT_REHIDRATACION)
    if data is None:
        return []
    vistos: list[str] = []

    def visitar(nodo, dentro_de_autor: bool = False) -> None:
        if isinstance(nodo, dict):
            for clave, valor in nodo.items():
                nombre = str(clave).casefold()
                if nombre in {"author", "authormeta", "user"} and isinstance(valor, dict):
                    crudo = valor.get("uniqueId") or valor.get("unique_id") or valor.get("name")
                    handle = normalizar_handle(str(crudo or ""))
                    if handle and handle not in vistos:
                        vistos.append(handle)
                    visitar(valor, True)
                elif dentro_de_autor and nombre in {"uniqueid", "unique_id"} and isinstance(valor, str):
                    handle = normalizar_handle(valor)
                    if handle and handle not in vistos:
                        vistos.append(handle)
                else:
                    visitar(valor, dentro_de_autor)
        elif isinstance(nodo, list):
            for item in nodo:
                visitar(item, dentro_de_autor)

    visitar(data)
    return vistos


def parece_bloqueo(html: str) -> bool:
    cuerpo = html or ""
    if "__UNIVERSAL_DATA_FOR_REHYDRATION__" in cuerpo or "__FRONTITY_CONNECT_STATE__" in cuerpo:
        return False
    texto = cuerpo.casefold()
    marcas = (
        "captcha",
        "verify you are human",
        "verify that you are",
        "please wait",
        "access denied",
        "just a moment",
        "tiktok.com/captcha",
    )
    return any(marca in texto for marca in marcas)
