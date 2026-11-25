"""Reglas de viralidad, slideshow y «vende algo».

La mediana sale de las publicaciones recientes del embed (las fijadas no
entran). Una pieza es viral si sus vistas son al menos el doble de esa
mediana y, además, llegan a un suelo absoluto. La cuenta pasa si vende algo
visible, al menos la mitad de la muestra es slideshow y hay una pieza viral.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from urllib.parse import urlparse

VISTAS_MINIMAS = 50_000
UMBRAL = 2.0
RATIO_SLIDESHOW = 0.5
RECIENTES = 10

_MENCION = re.compile(r"(?<![A-Za-z0-9._])@([A-Za-z0-9._]{2,24})")
_HASHTAG = re.compile(r"(?<![A-Za-z0-9_])#([A-Za-z0-9_]{2,40})")
_URL_TIKTOK = re.compile(r"tiktok\.com/@([A-Za-z0-9._]{2,24})", re.I)
_HANDLE = re.compile(r"^[a-z0-9._]{2,24}$")
_EMAIL = re.compile(r"\S+@\S+")
_DOMINIO = re.compile(r"\b(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9-]{1,40})\.(?:ai|app|com|co|io|shop|store)\b", re.I)

_HOSTS_GENERICOS = {
    "amazon",
    "amzn",
    "a",
    "apps",
    "apple",
    "beacons",
    "bio",
    "bit",
    "discord",
    "etsy",
    "facebook",
    "google",
    "gumroad",
    "instagram",
    "linktr",
    "linktree",
    "lnk",
    "onelink",
    "payhip",
    "pinterest",
    "play",
    "shopmy",
    "stan",
    "tiktok",
    "wa",
    "youtube",
    "youtu",
}

_APP = re.compile(
    r"apps\.apple|play\.google|onelink|testflight|\.app\b|\bapps?\b|app ?store|"
    r"download|descarga|📲|powered by ai|\.ai\b|aplicaci[oó]n",
    re.I,
)
_TIENDA = re.compile(
    r"amazon|amzn|a\.co/|ltk|shopmy|hotlinks|tiktok shop|showcase|🛒|🛍|"
    r"shop my|discount|% ?off|\bcode\b|c[oó]digo|serum|tienda|\bstore\b|\bshop\b",
    re.I,
)
_DIGITAL = re.compile(
    r"stan\.store|gumroad|beacons|ebook|e-book|\bguide|gu[ií]a|planner|template|"
    r"course|curso|payhip|etsy|paperback|\bbook\b|libro",
    re.I,
)
_WEB = re.compile(r"www\.|https?://|\.com\b|\.co\b|\.io\b", re.I)
_SOLO_RED = re.compile(
    r"\s*https?://(www\.)?(linktr\.ee|instagram\.com|youtube\.com|komi\.io)\S*\s*",
    re.I,
)
_APP_EN_PIE = re.compile(
    r"@[a-z0-9_.]*app\b|\bthe app\b|this app|esta app|\bapp\b|app ?store|"
    r"link in (my )?bio|in my bio|enlace en (mi )?bio|download",
    re.I,
)
_ES = re.compile(
    r"\b(que|para|con|los|las|una|por|tu|mi|cómo|qué|aquí|parejas|consejos)\b",
    re.I,
)
_EN = re.compile(r"\b(the|and|you|your|for|with|this|my|how|what)\b", re.I)


@dataclass
class Pieza:
    item_id: str
    url: str
    desc: str
    plays: int | None
    slideshow: bool
    created: str | None
    fijada: bool = False
    multiplo: float | None = None


@dataclass
class Evaluacion:
    estado: str
    motivo: str
    tipo_producto: str
    posts_muestreados: int | None
    fijados: int | None
    mediana_vistas: int | None
    slideshows_muestra: str
    es_cuenta_slideshow: str
    num_piezas_virales: int | None
    num_virales_no_fijadas: int | None
    mejor_reciente_url: str = ""
    mejor_reciente_vistas: int | None = None
    mejor_reciente_multiplo: float | None = None
    mejor_pieza_url: str = ""
    mejor_pieza_vistas: int | None = None
    mejor_pieza_multiplo: float | None = None
    mejor_pieza_slideshow: str = ""
    mejor_pieza_fijada: str = ""
    piezas_virales: str = ""
    ultima_publicacion: str = ""
    captions: list[str] = field(default_factory=list)
    idioma: str = ""


def normalizar_handle(valor: str) -> str:
    """Usuario de TikTok, en minúsculas, sin @ ni URL. Vacío si no vale."""
    text = (valor or "").strip()
    text = re.sub(r"^https?://(www\.)?tiktok\.com/", "", text, flags=re.I)
    text = text.split("?")[0].strip().strip("/")
    lower = text.casefold()
    if lower.startswith("embed/"):
        text = text.split("/", 1)[1]
    text = text.lstrip("@").split("/")[0].casefold().strip(".")
    if not _HANDLE.fullmatch(text):
        return ""
    return text


def menciones(texto: str) -> list[str]:
    vistos: list[str] = []
    for crudo in _MENCION.findall(texto or ""):
        handle = normalizar_handle(crudo)
        if handle and handle not in vistos:
            vistos.append(handle)
    return vistos


def hashtags(texto: str) -> list[str]:
    vistos: list[str] = []
    for crudo in _HASHTAG.findall(texto or ""):
        tag = crudo.casefold()
        if tag not in vistos and len(tag) >= 3:
            vistos.append(tag)
    return vistos


def handles_en_html(html: str) -> list[str]:
    vistos: list[str] = []
    for crudo in _URL_TIKTOK.findall(html or ""):
        handle = normalizar_handle(crudo)
        if handle and handle not in vistos:
            vistos.append(handle)
    return vistos


def slugs_de_texto(texto: str) -> list[str]:
    """Dominios propios que pueden ser un hashtag. No inventa cuentas."""
    vistos: list[str] = []
    for match in _DOMINIO.findall(texto or ""):
        slug = match.casefold()
        if slug in _HOSTS_GENERICOS or len(slug) < 4 or slug in vistos:
            continue
        vistos.append(slug)
    return vistos


def slugs_de_keyword(keyword: str) -> list[str]:
    palabras = re.findall(r"[a-z0-9]{5,}", (keyword or "").casefold())
    junto = re.sub(r"[^a-z0-9]+", "", (keyword or "").casefold())
    vistos: list[str] = []
    for slug in [*palabras, junto]:
        if len(slug) < 4 or slug in vistos:
            continue
        vistos.append(slug)
    return vistos[:3]


def tipo_producto_visible(bio: str, enlace: str, handle: str, nombre: str, pies: str = "") -> str:
    """Qué se está vendiendo, si se ve en el texto leído. Vacío si no se ve."""
    firma = bio or ""
    link = (enlace or "").strip()
    conjunto = f"{firma} {link} {handle or ''} {nombre or ''}"
    sin_email = _EMAIL.sub("", conjunto)
    if _APP.search(sin_email):
        return "app"
    if _TIENDA.search(sin_email):
        return "ecommerce (tienda/afiliados)"
    if _DIGITAL.search(sin_email):
        return "producto digital"
    if _WEB.search(sin_email) and not _SOLO_RED.fullmatch(link or "x"):
        return "web propia"
    if _APP_EN_PIE.search(pies or ""):
        return "app/producto (solo en pies de post)"
    if link:
        return "enlace sin producto claro"
    return ""


def idioma_de(texto: str, *, idioma_perfil: str = "") -> str:
    es = len(_ES.findall(texto or ""))
    en = len(_EN.findall(texto or ""))
    if es > en:
        return "es"
    if en > es:
        return "en"
    perfil = (idioma_perfil or "").casefold()
    if perfil.startswith("es"):
        return "es"
    if perfil.startswith("en"):
        return "en"
    return ""


def _entero_vistas(valor) -> int | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if valor >= 0 else None
    if isinstance(valor, float):
        if valor < 0 or not valor.is_integer():
            return None
        return int(valor)
    text = str(valor).strip().replace(" ", "")
    if not text or not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def marcar_fijadas(piezas: list[Pieza]) -> int:
    """El embed pone las fijadas delante y luego unas 10 recientes.

    Si alguna pieza trae la marca explícita, manda esa marca. Si no, las que
    sobran por encima de 10 se tratan como fijadas: es el convenio del embed,
    no una suposición sobre el resto del perfil.
    """
    if any(pieza.fijada for pieza in piezas):
        return sum(1 for pieza in piezas if pieza.fijada)
    extra = max(0, len(piezas) - RECIENTES)
    for indice, pieza in enumerate(piezas):
        pieza.fijada = indice < extra
    return extra


def evaluar(
    piezas: list[Pieza],
    *,
    bio: str = "",
    enlace: str = "",
    handle: str = "",
    nombre: str = "",
    idioma_perfil: str = "",
    privada: bool = False,
    vistas_minimas: int = VISTAS_MINIMAS,
    umbral: float = UMBRAL,
    ratio_slideshow: float = RATIO_SLIDESHOW,
) -> Evaluacion:
    captions = [pieza.desc for pieza in piezas if pieza.desc]
    pies = " ".join(captions)
    tipo = tipo_producto_visible(bio, enlace, handle, nombre, pies)
    idioma = idioma_de(f"{bio}\n{pies}", idioma_perfil=idioma_perfil)
    if privada:
        return Evaluacion(
            estado="descartada",
            motivo="cuenta privada",
            tipo_producto=tipo,
            posts_muestreados=len(piezas) or None,
            fijados=None,
            mediana_vistas=None,
            slideshows_muestra="",
            es_cuenta_slideshow="",
            num_piezas_virales=None,
            num_virales_no_fijadas=None,
            captions=captions,
            idioma=idioma,
        )
    if not piezas:
        return Evaluacion(
            estado="descartada",
            motivo="sin datos accesibles (sin publicaciones en el embed)",
            tipo_producto=tipo,
            posts_muestreados=0,
            fijados=0,
            mediana_vistas=None,
            slideshows_muestra="0/0",
            es_cuenta_slideshow="no",
            num_piezas_virales=0,
            num_virales_no_fijadas=0,
            captions=captions,
            idioma=idioma,
        )

    fijados = marcar_fijadas(piezas)
    recientes = [pieza for pieza in piezas if not pieza.fijada]
    plays = [pieza.plays for pieza in recientes if pieza.plays is not None]
    mediana = float(statistics.median(plays)) if plays else None
    mediana_entera = int(mediana) if mediana is not None else None
    for pieza in piezas:
        if mediana and pieza.plays is not None and mediana > 0:
            pieza.multiplo = round(pieza.plays / mediana, 1)
        else:
            pieza.multiplo = None

    slides = sum(1 for pieza in piezas if pieza.slideshow)
    n = len(piezas)
    ratio = slides / n
    virales = [
        pieza
        for pieza in piezas
        if pieza.plays is not None
        and mediana is not None
        and mediana > 0
        and pieza.plays >= umbral * mediana
        and pieza.plays >= vistas_minimas
    ]
    virales.sort(key=lambda pieza: pieza.plays or 0, reverse=True)
    no_fijadas = [pieza for pieza in virales if not pieza.fijada]
    razones: list[str] = []
    if not tipo or tipo == "enlace sin producto claro":
        razones.append("no vende nada visible (bio sin app/tienda/producto)")
    if ratio < ratio_slideshow:
        razones.append(f"no es cuenta de slideshows ({slides}/{n})")
    if not virales:
        solo_multiplo = [
            pieza
            for pieza in piezas
            if pieza.plays is not None
            and mediana is not None
            and mediana > 0
            and pieza.plays >= umbral * mediana
        ]
        if not plays or mediana is None or mediana <= 0:
            razones.append("sin vistas en la muestra reciente")
        elif not solo_multiplo:
            razones.append("ninguna pieza ≥2× mediana")
        else:
            tope = max(pieza.plays or 0 for pieza in solo_multiplo)
            razones.append(
                f"piezas ≥2× pero alcance bajo (máx {tope:,} vistas < {vistas_minimas:,})"
            )
    mejor = virales[0] if virales else None
    reciente = no_fijadas[0] if no_fijadas else None
    fechas = [pieza.created for pieza in piezas if pieza.created]
    return Evaluacion(
        estado="califica" if not razones else "descartada",
        motivo="; ".join(razones),
        tipo_producto=tipo,
        posts_muestreados=n,
        fijados=fijados,
        mediana_vistas=mediana_entera,
        slideshows_muestra=f"{slides}/{n}",
        es_cuenta_slideshow="sí" if ratio >= ratio_slideshow else "no",
        num_piezas_virales=len(virales),
        num_virales_no_fijadas=len(no_fijadas),
        mejor_reciente_url=reciente.url if reciente else "",
        mejor_reciente_vistas=reciente.plays if reciente else None,
        mejor_reciente_multiplo=reciente.multiplo if reciente else None,
        mejor_pieza_url=mejor.url if mejor else "",
        mejor_pieza_vistas=mejor.plays if mejor else None,
        mejor_pieza_multiplo=mejor.multiplo if mejor else None,
        mejor_pieza_slideshow=("sí" if mejor.slideshow else "no (vídeo)") if mejor else "",
        mejor_pieza_fijada=("sí" if mejor.fijada else "no") if mejor else "",
        piezas_virales=_formatear_piezas(virales),
        ultima_publicacion=max(fechas) if fechas else "",
        captions=captions,
        idioma=idioma,
    )


def _formatear_piezas(virales: list[Pieza]) -> str:
    trozos: list[str] = []
    for pieza in virales[:5]:
        marcas = ""
        if pieza.fijada:
            marcas += " | fijado"
        if not pieza.slideshow:
            marcas += " | vídeo"
        vistas = f"{pieza.plays:,}" if pieza.plays is not None else ""
        trozos.append(f"{pieza.url} | {vistas} vistas | {pieza.multiplo}×{marcas}")
    return " ; ".join(trozos)


def entero_vistas(valor) -> int | None:
    return _entero_vistas(valor)


def host_de_enlace(enlace: str) -> str:
    bruto = (enlace or "").strip()
    if not bruto:
        return ""
    if "://" not in bruto:
        bruto = "https://" + bruto
    host = (urlparse(bruto).hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    return host
