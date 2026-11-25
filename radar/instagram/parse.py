"""Lectura de los JSON que deja la pestaña Reels. No completa huecos."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from radar.instagram.reglas import Reel, entero_vistas, normalizar_handle

_CLAVES_MEDIO = (
    "play_count",
    "ig_play_count",
    "video_view_count",
    "product_type",
    "media_type",
    "like_count",
)
_CLAVES_PERFIL = ("follower_count", "edge_followed_by", "biography", "full_name", "is_verified")
_CLAVES_SUGERIDOS = {"edge_related_profiles", "edge_chaining", "suggested_users", "similar_users"}
_CLAVES_VISTAS = ("play_count", "ig_play_count", "view_count", "video_view_count")


@dataclass
class Lectura:
    handle: str
    nombre: str = ""
    bio: str = ""
    enlace: str = ""
    seguidores: int | None = None
    verificado: bool | None = None
    reels: list[Reel] = field(default_factory=list)
    sugeridos: list[str] = field(default_factory=list)
    colaboradores: list[str] = field(default_factory=list)
    privada: bool = False
    error: str = ""
    crudo: dict = field(default_factory=dict)


def parsear_blobs(blobs: list, handle: str) -> Lectura:
    """Une las respuestas graphql/api y los script JSON de una ficha."""
    limpio = normalizar_handle(handle) or (handle or "").strip().lstrip("@").casefold()
    perfiles: list[dict] = []
    medios: list[dict] = []
    sugeridos: list[str] = []
    viewers: list[str] = []
    for blob in blobs or []:
        _walk(blob, limpio, perfiles, medios, sugeridos, viewers)
    perfil = _elegir_perfil(perfiles)
    lectura = Lectura(handle=limpio)
    if perfil:
        lectura.nombre = str(perfil.get("full_name") or "")
        lectura.bio = str(perfil.get("biography") or "")
        lectura.enlace = str(perfil.get("external_url") or "")
        lectura.seguidores = _seguidores(perfil)
        if "is_verified" in perfil and isinstance(perfil.get("is_verified"), bool):
            lectura.verificado = perfil["is_verified"]
    vistos: dict[str, Reel] = {}
    colaboradores: list[str] = []
    for nodo in medios:
        reel = _a_reel(nodo, limpio)
        if reel is None:
            continue
        previo = vistos.get(reel.code)
        if previo is None:
            vistos[reel.code] = reel
        else:
            vistos[reel.code] = _fusionar(previo, reel)
        for colab in _colaboradores(nodo):
            _anadir(colaboradores, colab, propio=limpio, viewers=viewers)
    lectura.reels = list(vistos.values())
    propios = {limpio, *viewers}
    lectura.sugeridos = [h for h in _unicos(sugeridos) if h not in propios]
    lectura.colaboradores = [h for h in colaboradores if h not in propios]
    lectura.crudo = {
        "seguidores": lectura.seguidores,
        "verificado": lectura.verificado,
        "reels": [
            {"code": reel.code, "plays": reel.plays, "fijado": reel.fijado, "taken_at": reel.taken_at}
            for reel in lectura.reels
        ],
        "sugeridos": lectura.sugeridos,
        "colaboradores": lectura.colaboradores,
    }
    return lectura


def handles_en_html(html: str) -> list[str]:
    return _handles_en_html(html)


def _handles_en_html(html: str) -> list[str]:
    import re

    vistos: list[str] = []
    for ruta in re.findall(r"instagram\.com/([A-Za-z0-9._/]{1,80})", html or "", flags=re.I):
        partes = [parte for parte in ruta.split("/") if parte]
        if not partes:
            continue
        handle = normalizar_handle(partes[0])
        if handle and handle not in vistos:
            vistos.append(handle)
    return vistos


def _walk(nodo, handle: str, perfiles: list[dict], medios: list[dict], sugeridos: list[str], viewers: list[str]) -> None:
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            nombre = str(clave)
            if nombre in {"viewer", "logged_in_user"} and isinstance(valor, dict):
                _anadir(viewers, str(valor.get("username") or ""), propio="", viewers=[])
                continue
            if nombre in _CLAVES_SUGERIDOS:
                _usernames(valor, sugeridos)
                continue
            _walk(valor, handle, perfiles, medios, sugeridos, viewers)
        if _es_perfil(nodo, handle):
            perfiles.append(nodo)
        if _parece_medio(nodo):
            medios.append(nodo)
    elif isinstance(nodo, list):
        for item in nodo:
            _walk(item, handle, perfiles, medios, sugeridos, viewers)
    elif isinstance(nodo, str) and nodo[:1] in "{[":
        texto = nodo.strip()
        if "play_count" in texto or "biography" in texto or "shortcode" in texto or '"code"' in texto:
            try:
                data = json.loads(texto)
            except json.JSONDecodeError:
                return
            _walk(data, handle, perfiles, medios, sugeridos, viewers)


def _es_perfil(nodo: dict, handle: str) -> bool:
    if str(nodo.get("username") or "").casefold() != handle.casefold():
        return False
    return any(clave in nodo for clave in _CLAVES_PERFIL)


def _parece_medio(nodo: dict) -> bool:
    if not (nodo.get("code") or nodo.get("shortcode")):
        return False
    return any(clave in nodo for clave in _CLAVES_MEDIO)


def _elegir_perfil(perfiles: list[dict]) -> dict | None:
    if not perfiles:
        return None
    for perfil in perfiles:
        if _seguidores(perfil) is not None:
            return perfil
    return perfiles[0]


def _seguidores(nodo: dict) -> int | None:
    if "follower_count" in nodo:
        directo = entero_vistas(nodo.get("follower_count"))
        if directo is not None:
            return directo
    edge = nodo.get("edge_followed_by")
    if isinstance(edge, dict):
        return entero_vistas(edge.get("count"))
    return None


def _a_reel(nodo: dict, handle: str) -> Reel | None:
    dueno = _dueno(nodo)
    if dueno and dueno != handle:
        return None
    if not _es_reel(nodo):
        return None
    code = str(nodo.get("code") or nodo.get("shortcode") or "").strip()
    if not code or "/" in code or len(code) > 40:
        return None
    return Reel(
        code=code,
        url=f"https://www.instagram.com/reel/{code}/",
        plays=_plays(nodo),
        caption=_pie(nodo),
        taken_at=_fecha(nodo.get("taken_at") if "taken_at" in nodo else nodo.get("taken_at_timestamp")),
        fijado=_fijado(nodo),
    )


def _dueno(nodo: dict) -> str:
    user = nodo.get("user") if isinstance(nodo.get("user"), dict) else None
    if user is None and isinstance(nodo.get("owner"), dict):
        user = nodo["owner"]
    if isinstance(user, dict) and user.get("username"):
        return normalizar_handle(str(user.get("username")))
    return ""


def _es_reel(nodo: dict) -> bool:
    product = str(nodo.get("product_type") or nodo.get("productType") or "").casefold()
    if product in {"carousel_container", "sidecar", "feed"}:
        return False
    tipo = nodo.get("media_type")
    if tipo == 8 or nodo.get("carousel_media_count") or isinstance(nodo.get("carousel_media"), list):
        return False
    typename = str(nodo.get("__typename") or "").casefold()
    if typename == "graphsidecar":
        return False
    return product == "clips" or tipo == 2


def _plays(nodo: dict) -> int | None:
    for clave in _CLAVES_VISTAS:
        if clave not in nodo:
            continue
        valor = entero_vistas(nodo.get(clave))
        if valor is not None:
            return valor
    return None


def _fijado(nodo: dict) -> bool:
    for clave in ("pinned", "is_pinned", "isPinned"):
        valor = nodo.get(clave)
        if valor is True or valor == 1:
            return True
        if isinstance(valor, str) and valor.strip().casefold() in {"1", "true", "yes", "sí", "si"}:
            return True
    for clave in ("timeline_pinned_user_ids", "clips_tab_pinned_user_ids", "pinned_for_users"):
        valor = nodo.get(clave)
        if isinstance(valor, list) and len(valor) > 0:
            return True
    for clave in ("pin_icon", "pinIcon", "pinned_badge"):
        if nodo.get(clave):
            return True
    return False


def _fecha(valor) -> str | None:
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        segundos = int(valor)
        if segundos < 1_000_000_000 or segundos > 4_000_000_000:
            return None
        return time.strftime("%Y-%m-%d", time.gmtime(segundos))
    texto = str(valor).strip()
    if len(texto) >= 10 and texto[4] == "-" and texto[7] == "-":
        return texto[:10]
    return None


def _pie(nodo: dict) -> str:
    caption = nodo.get("caption")
    if isinstance(caption, dict):
        return str(caption.get("text") or "")
    if isinstance(caption, str):
        return caption
    edge = nodo.get("edge_media_to_caption")
    if isinstance(edge, dict):
        edges = edge.get("edges") or []
        if edges and isinstance(edges[0], dict):
            node = edges[0].get("node") if isinstance(edges[0].get("node"), dict) else {}
            return str(node.get("text") or "")
    return ""


def _colaboradores(nodo: dict) -> list[str]:
    encontrados: list[str] = []
    for clave in ("coauthor_producers", "invited_coauthors"):
        valor = nodo.get(clave)
        if isinstance(valor, list):
            for item in valor:
                if isinstance(item, dict):
                    _anadir(encontrados, str(item.get("username") or ""), propio="", viewers=[])
    tags = nodo.get("usertags")
    if isinstance(tags, dict):
        for item in tags.get("in") or []:
            if isinstance(item, dict):
                user = item.get("user") if isinstance(item.get("user"), dict) else item
                _anadir(encontrados, str(user.get("username") or ""), propio="", viewers=[])
    edge = nodo.get("edge_media_to_tagged_user")
    if isinstance(edge, dict):
        for item in edge.get("edges") or []:
            if not isinstance(item, dict):
                continue
            node = item.get("node") if isinstance(item.get("node"), dict) else {}
            user = node.get("user") if isinstance(node.get("user"), dict) else node
            _anadir(encontrados, str(user.get("username") or ""), propio="", viewers=[])
    return encontrados


def _usernames(nodo, salida: list[str]) -> None:
    if isinstance(nodo, dict):
        if "username" in nodo:
            _anadir(salida, str(nodo.get("username") or ""), propio="", viewers=[])
        for valor in nodo.values():
            _usernames(valor, salida)
    elif isinstance(nodo, list):
        for item in nodo:
            _usernames(item, salida)


def _anadir(salida: list[str], crudo: str, *, propio: str, viewers: list[str]) -> None:
    handle = normalizar_handle(crudo)
    if not handle or handle == propio or handle in viewers or handle in salida:
        return
    salida.append(handle)


def _unicos(handles: list[str]) -> list[str]:
    vistos: list[str] = []
    for handle in handles:
        limpio = normalizar_handle(handle)
        if limpio and limpio not in vistos:
            vistos.append(limpio)
    return vistos


def _fusionar(previo: Reel, nuevo: Reel) -> Reel:
    plays = nuevo.plays if nuevo.plays is not None else previo.plays
    if previo.plays is not None and nuevo.plays is not None:
        plays = max(previo.plays, nuevo.plays)
    return Reel(
        code=previo.code,
        url=previo.url,
        plays=plays,
        caption=nuevo.caption or previo.caption,
        taken_at=nuevo.taken_at or previo.taken_at,
        fijado=previo.fijado or nuevo.fijado,
    )
