"""Lee el texto de las piezas virales. Sin --yes no hay red."""

from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from radar.carrusel import descargar_imagen, url_de_imagen_permitida
from radar.config import Settings
from radar.db import Database, utc_now
from radar.ganchos.fotos import aplicar_ocr, html_con_navegador, leer_slideshow, pistas_de_producto
from radar.ganchos.ig import PlaywrightLectorReel, ReelCrudo
from radar.ganchos.piezas import Candidata, candidatas_de_cuentas
from radar.ganchos.store import guardar_pieza, obtener_por_url
from radar.hooks import Segment, extract_hook, first_sentence
from radar.instagram.fuente import InstagramBloqueado, InstagramSesionAusente, pausar as pausar_ig
from radar.ocr import OcrNotInstalled, leer_imagen
from radar.tiktok.fuente import HttpxTransporte, Lector, TikTokBloqueado, pausar
from radar.transcribe import DownloadError, WhisperNotInstalled, download_media, transcribe_file


@dataclass
class Informe:
    status: str
    mensaje: str
    leidas: int = 0
    omitidas: int = 0
    errores: int = 0
    bloqueado: bool = False
    avisos: list[str] = field(default_factory=list)


def planificar(
    db: Database,
    fuente: str,
    maximo: int | None,
    settings: Settings,
    *,
    reintentar: bool = False,
) -> tuple[list[Candidata], list[Candidata], str]:
    cola, fijadas = _cola(db, fuente, maximo, settings, reintentar=reintentar)
    lineas = [
        f"Piezas a leer: {len(cola)}. Fijadas que no se leen: {len(fijadas)}.",
        "Sin --yes no hay ninguna petición.",
    ]
    for pieza in cola:
        vistas = "—" if pieza.vistas is None else str(pieza.vistas)
        multiplo = "—" if pieza.multiplo is None else f"{pieza.multiplo}×"
        lineas.append(f"  {pieza.fuente}  {multiplo}  {vistas}  {pieza.url}")
    if fuente in {"instagram", "todas"} and not settings.browser_ready:
        lineas.append("Falta la sesión del navegador. Para Instagram hace falta `radar login` en esta máquina.")
    return cola, fijadas, "\n".join(lineas)


def ejecutar(
    db: Database,
    settings: Settings,
    *,
    fuente: str,
    maximo: int | None,
    permitir_red: bool,
    leer_tiktok=None,
    leer_instagram=None,
    ocr=None,
    transcribir=None,
    descargar=None,
    dormir=None,
    rng: random.Random | None = None,
    reintentar: bool = False,
    navegador=None,
) -> Informe:
    if fuente not in {"tiktok", "instagram", "todas"}:
        raise ValueError("La fuente tiene que ser tiktok, instagram o todas.")
    cola, fijadas, texto = planificar(db, fuente, maximo, settings, reintentar=reintentar)
    if not permitir_red:
        return Informe(status="plan", mensaje=texto, omitidas=len(fijadas))
    azar = rng or random.Random()
    espera = dormir or _dormir
    motor = ocr or leer_imagen
    bajar = descargar or _bajar
    hechas = 0
    fallos = 0
    avisos: list[str] = []
    lector = None
    sesion = None
    try:
        for candidata in fijadas:
            if obtener_por_url(db, candidata.fuente, candidata.url):
                continue
            _guardar_omitida(db, candidata)
        for indice, candidata in enumerate(cola):
            previa = obtener_por_url(db, candidata.fuente, candidata.url)
            if not reintentar and previa and _completa(previa):
                continue
            if candidata.fuente == "instagram" and leer_instagram is None and not settings.browser_ready:
                if not any("navegador" in aviso for aviso in avisos):
                    avisos.append("Falta la sesión del navegador. No se ha abierto Instagram.")
                continue
            if indice:
                _pausa_de(candidata.fuente, settings, azar, espera)
            cuenta_como_leida = True
            try:
                if candidata.fuente == "tiktok":
                    if leer_tiktok is None:
                        if lector is None:
                            lector = Lector(HttpxTransporte(), dormir=espera, rng=azar)
                        estado, aviso = _extraer_tiktok(
                            db,
                            candidata,
                            lector,
                            settings,
                            motor,
                            bajar,
                            azar,
                            espera,
                            navegador=html_con_navegador if navegador is None else navegador,
                        )
                    else:
                        estado, aviso = _extraer_tiktok_inyectado(db, candidata, leer_tiktok, motor, bajar)
                    if estado != "extraida":
                        fallos += 1
                        cuenta_como_leida = False
                        avisos.append(f"{candidata.url}: {aviso}")
                else:
                    if leer_instagram is None:
                        if sesion is None:
                            sesion = PlaywrightLectorReel(settings.browser_profile)
                        crudo = sesion.leer(candidata.url)
                    else:
                        crudo = leer_instagram(candidata.url)
                    if crudo.fijado:
                        _guardar_omitida(db, candidata)
                        cuenta_como_leida = False
                    else:
                        _guardar_reel(db, settings, candidata, crudo, motor, bajar, transcribir)
            except (TikTokBloqueado, InstagramBloqueado, InstagramSesionAusente) as exc:
                return Informe(
                    status="bloqueado",
                    mensaje=f"{exc} Leídas en esta pasada: {hechas}.",
                    leidas=hechas,
                    omitidas=len(fijadas),
                    errores=fallos,
                    bloqueado=True,
                    avisos=avisos,
                )
            except OcrNotInstalled as exc:
                avisos.append(str(exc))
                fallos += 1
                _guardar_error(db, candidata, str(exc))
                continue
            if cuenta_como_leida:
                hechas += 1
    finally:
        if lector is not None:
            lector.transporte.close()
        if sesion is not None:
            sesion.close()
    lineas = [f"Leídas {hechas}. Fijadas omitidas: {len(fijadas)}. Errores: {fallos}."]
    lineas.extend(avisos)
    return Informe(
        status="ok",
        mensaje="\n".join(lineas),
        leidas=hechas,
        omitidas=len(fijadas),
        errores=fallos,
        avisos=avisos,
    )


def _cola(
    db: Database,
    fuente: str,
    maximo: int | None,
    settings: Settings,
    *,
    reintentar: bool = False,
) -> tuple[list[Candidata], list[Candidata]]:
    fuentes = ("tiktok", "instagram") if fuente == "todas" else (fuente,)
    todas: list[Candidata] = []
    for nombre in fuentes:
        cuentas = _cuentas(db, nombre)
        todas.extend(candidatas_de_cuentas(cuentas, fuente=nombre))
    todas.sort(key=lambda pieza: (-(pieza.multiplo if pieza.multiplo is not None else -1), -(pieza.vistas or -1), pieza.url))
    hechas = set() if reintentar else _ya_extraidas(db)
    fijadas = [pieza for pieza in todas if pieza.fijada]
    sueltas = [pieza for pieza in todas if not pieza.fijada and (pieza.fuente, pieza.url) not in hechas]
    if maximo is None:
        tope_tt = settings.tt_max_cuentas
        tope_ig = settings.ig_creadores_max
        elegidas: list[Candidata] = []
        n_tt = 0
        n_ig = 0
        for pieza in sueltas:
            if pieza.fuente == "tiktok" and n_tt >= tope_tt:
                continue
            if pieza.fuente == "instagram" and n_ig >= tope_ig:
                continue
            elegidas.append(pieza)
            if pieza.fuente == "tiktok":
                n_tt += 1
            else:
                n_ig += 1
        return elegidas, fijadas
    return sueltas[: max(maximo, 0)], fijadas


def _completa(pieza: dict) -> bool:
    if pieza.get("estado") != "extraida":
        return False
    if pieza.get("fuente") == "tiktok":
        return bool(pieza.get("num_diapositivas")) and bool(str(pieza.get("gancho_texto") or "").strip())
    return bool(pieza.get("gancho_texto") or pieza.get("transcripcion") or pieza.get("texto_pantalla"))


def _ya_extraidas(db: Database) -> set[tuple[str, str]]:
    """Una TikTok sin diapositivas o sin texto no cuenta: se vuelve a leer."""
    with db.connect() as conn:
        filas = conn.execute(
            """
            SELECT fuente, url FROM gh_piezas
            WHERE estado = 'extraida'
              AND (
                (fuente = 'tiktok' AND COALESCE(num_diapositivas, 0) > 0 AND gancho_texto != '')
                OR (fuente != 'tiktok' AND (gancho_texto != '' OR transcripcion != '' OR texto_pantalla != ''))
              )
            """
        ).fetchall()
    return {(fila["fuente"], fila["url"]) for fila in filas}


def _cuentas(db: Database, fuente: str) -> list[dict]:
    if fuente == "tiktok":
        from radar.tiktok.store import listar_cuentas

        return listar_cuentas(db, estado="califica")
    from radar.instagram.store import listar_cuentas

    return listar_cuentas(db, estado="califica")


def _resultado_tiktok(lectura, diapositivas: list[dict], aviso: str) -> tuple[str, str, str]:
    gancho = next((item["texto"] for item in diapositivas if item.get("texto")), "")
    if not diapositivas:
        return "error", "", aviso or lectura.error or "Sin imágenes públicas del slideshow. Queda pendiente."
    if not gancho:
        return (
            "error",
            "",
            "El OCR no leyó texto en las diapositivas. No se inventa el gancho. Queda pendiente.",
        )
    return "extraida", gancho, aviso


def _guardar_lectura_tiktok(db, candidata: Candidata, lectura, diapositivas, producto, cta, aviso: str) -> tuple[str, str]:
    estado, gancho, aviso_final = _resultado_tiktok(lectura, diapositivas, aviso)
    guardar_pieza(
        db,
        {
            "fuente": "tiktok",
            "url": candidata.url,
            "handle": candidata.handle,
            "nicho": candidata.nicho,
            "subnicho": candidata.subnicho,
            "vistas": candidata.vistas,
            "mediana": candidata.mediana,
            "multiplo": candidata.multiplo,
            "fijada": False,
            "caption": getattr(lectura, "caption", "") or "",
            "num_diapositivas": len(diapositivas),
            "diapositivas": diapositivas,
            "producto_indice": producto,
            "cta": cta,
            "gancho_texto": gancho,
            "estado": estado,
            "error": "" if estado == "extraida" else aviso_final,
            "extraida_en": utc_now() if estado == "extraida" else "",
        },
    )
    return estado, aviso_final


def _extraer_tiktok(db, candidata, lector, settings, ocr, descargar, rng, dormir, navegador=None) -> tuple[str, str]:
    lectura = leer_slideshow(
        lector,
        candidata.url,
        pausa=settings.tt_pause,
        dormir=dormir,
        rng=rng,
        navegador=navegador,
    )
    with tempfile.TemporaryDirectory(prefix="radar-gh-") as carpeta:
        diapositivas, producto, cta, aviso = aplicar_ocr(
            lectura,
            ocr=ocr,
            descargar=descargar,
            workdir=Path(carpeta),
            pistas=pistas_de_producto(candidata.que_vende, candidata.enlace),
        )
    return _guardar_lectura_tiktok(db, candidata, lectura, diapositivas, producto, cta, aviso)


def _extraer_tiktok_inyectado(db, candidata: Candidata, leer, ocr, descargar) -> tuple[str, str]:
    """El test devuelve una LecturaFoto. El OCR sigue siendo el de la app."""
    lectura = leer(candidata.url)
    with tempfile.TemporaryDirectory(prefix="radar-gh-") as carpeta:
        diapositivas, producto, cta, aviso = aplicar_ocr(
            lectura,
            ocr=ocr,
            descargar=descargar,
            workdir=Path(carpeta),
            pistas=pistas_de_producto(candidata.que_vende, candidata.enlace),
        )
    return _guardar_lectura_tiktok(db, candidata, lectura, diapositivas, producto, cta, aviso)


def _guardar_reel(db, settings: Settings, candidata: Candidata, crudo: ReelCrudo, ocr, descargar, transcribir) -> None:
    texto = crudo.transcripcion or ""
    segmentos = list(crudo.segmentos or [])
    error = crudo.error or ""
    if not texto and crudo.video_url:
        texto, segmentos, fallo = _transcribir(crudo.video_url, settings, transcribir)
        error = error or fallo
    pantalla = crudo.texto_pantalla
    if not pantalla and crudo.cover_url and url_de_imagen_permitida(crudo.cover_url):
        with tempfile.TemporaryDirectory(prefix="radar-gh-ig-") as carpeta:
            local = (descargar or descargar_imagen)(crudo.cover_url, Path(carpeta), 1)
            if local is not None:
                try:
                    pantalla = (ocr(local) or "").strip()
                except OcrNotInstalled:
                    raise
                except OSError:
                    pantalla = ""
                local.unlink(missing_ok=True)
    hablado = ""
    if texto:
        hablado = extract_hook(texto, [Segment(float(item["start"]), float(item["end"]), item["text"]) for item in segmentos if "start" in item])
        if not hablado:
            hablado = first_sentence(texto)
    en_pantalla = first_sentence(pantalla)
    if hablado and en_pantalla:
        gancho = f"{hablado} | en pantalla: {en_pantalla}"
    else:
        gancho = hablado or en_pantalla
    estado = "extraida" if (gancho or candidata.url and (texto or crudo.caption or pantalla)) else "error"
    guardar_pieza(
        db,
        {
            "fuente": "instagram",
            "url": candidata.url,
            "handle": candidata.handle,
            "nicho": candidata.nicho,
            "subnicho": candidata.subnicho,
            "vistas": candidata.vistas,
            "mediana": candidata.mediana,
            "multiplo": candidata.multiplo,
            "caption": crudo.caption,
            "transcripcion": texto,
            "texto_pantalla": pantalla,
            "gancho_texto": gancho,
            "num_diapositivas": None,
            "estado": estado,
            "error": error,
            "extraida_en": utc_now() if estado == "extraida" else "",
        },
    )


def _transcribir(url: str, settings: Settings, transcribir) -> tuple[str, list[dict], str]:
    with tempfile.TemporaryDirectory(prefix="radar-gh-audio-") as carpeta:
        destino = Path(carpeta) / "reel.media"
        try:
            download_media(url, destino)
            if transcribir is None:
                resultado = transcribe_file(
                    destino,
                    model_name=settings.whisper_model,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                    language=settings.whisper_language,
                )
            else:
                resultado = transcribir(destino)
        except WhisperNotInstalled as exc:
            return "", [], str(exc)
        except (DownloadError, OSError, RuntimeError) as exc:
            return "", [], str(exc)
    return resultado.text, list(resultado.segments), ""


def _guardar_omitida(db, candidata: Candidata) -> None:
    guardar_pieza(
        db,
        {
            "fuente": candidata.fuente,
            "url": candidata.url,
            "handle": candidata.handle,
            "nicho": candidata.nicho,
            "subnicho": candidata.subnicho,
            "vistas": candidata.vistas,
            "mediana": candidata.mediana,
            "multiplo": candidata.multiplo,
            "fijada": True,
            "estado": "omitida",
            "error": "Publicación fijada: no entra en la mediana ni se extrae.",
            "extraida_en": "",
        },
    )


def _guardar_error(db, candidata: Candidata, mensaje: str) -> None:
    guardar_pieza(
        db,
        {
            "fuente": candidata.fuente,
            "url": candidata.url,
            "handle": candidata.handle,
            "nicho": candidata.nicho,
            "subnicho": candidata.subnicho,
            "vistas": candidata.vistas,
            "mediana": candidata.mediana,
            "multiplo": candidata.multiplo,
            "estado": "error",
            "error": mensaje,
        },
    )


def _pausa_de(fuente: str, settings: Settings, rng: random.Random, dormir) -> None:
    tramo = settings.tt_pause if fuente == "tiktok" else settings.ig_creadores_pause
    if fuente == "tiktok":
        pausar(rng, dormir, tramo)
    else:
        pausar_ig(rng, dormir, tramo)


def _bajar(url: str, workdir: Path, indice: int) -> Path | None:
    from radar.ganchos.fotos import host_de_imagen_permitido

    if host_de_imagen_permitido(url):
        return _bajar_tiktok(url, workdir, indice)
    return descargar_imagen(url, workdir, indice)


def _bajar_tiktok(url: str, workdir: Path, indice: int) -> Path | None:
    import httpx

    destino = workdir / f"diapositiva-{indice}.img"
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            respuesta = client.get(url)
    except httpx.HTTPError:
        return None
    if respuesta.status_code != 200 or not respuesta.content:
        return None
    destino.write_bytes(respuesta.content)
    return destino


def _dormir(segundos: float) -> None:
    import time

    time.sleep(segundos)
