"""Descubre cuentas y refresca las que ya están, sin inventar cifras."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from radar.db import Database, utc_now
from radar.tiktok.apify_opcional import buscar_handles
from radar.tiktok.clasificar import Clasificador
from radar.tiktok.fuente import Lector, TikTokBloqueado, Transporte, pausar, url_hashtag
from radar.tiktok.reglas import evaluar, hashtags, menciones, normalizar_handle, slugs_de_keyword, slugs_de_texto
from radar.tiktok.store import (
    anadir_semilla,
    asegurar_semillas_defecto,
    contar_por_estado,
    guardar_cuenta,
    handles_conocidos,
    listar_cuentas,
    listar_semillas,
    marcar_semilla_leida,
    obtener_cuenta,
    pendientes,
    registrar_pasada,
)

FUENTE = "TikTok público (página de perfil + embed oficial tiktok.com/embed/@usuario)"


@dataclass
class Informe:
    status: str
    mensaje: str
    leidas: int = 0
    nuevas: int = 0
    califican: int = 0
    descartadas: int = 0
    candidatas: int = 0
    bloqueado: bool = False
    avisos: list[str] = field(default_factory=list)


def hoy() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def encolar_handle(db: Database, handle: str, semilla: str, conocidas: set[str]) -> bool:
    limpio = normalizar_handle(handle)
    if not limpio or limpio in conocidas:
        return False
    guardar_cuenta(
        db,
        {
            "handle": limpio,
            "estado": "candidata",
            "semilla": semilla,
            "fecha_detectada": hoy(),
            "fecha_actualizada": "",
        },
    )
    conocidas.add(limpio)
    return True


def bola_de_nieve_local(db: Database, conocidas: set[str]) -> int:
    """Menciones ya leídas en bios y pies. No sale a la red."""
    nuevas = 0
    for cuenta in listar_cuentas(db, estado="califica"):
        pies = "\n".join(str(pie) for pie in cuenta.get("captions") or [])
        texto = "\n".join([cuenta.get("bio") or "", cuenta.get("enlace_bio") or "", pies])
        for handle in menciones(texto):
            if handle == cuenta["handle"]:
                continue
            if encolar_handle(db, handle, f"mencion:@{cuenta['handle']}", conocidas):
                nuevas += 1
    for semilla in listar_semillas(db):
        if semilla["tipo"] == "handle" and encolar_handle(
            db, semilla["valor"], f"semilla:{semilla['valor']}", conocidas
        ):
            nuevas += 1
    return nuevas


def _leer_y_guardar(
    db: Database,
    lector: Lector,
    handle: str,
    *,
    semilla: str,
    clasificador: Clasificador | None,
    vistas_minimas: int,
    conservar_manual: bool,
    rng: random.Random,
    dormir,
    pausa: tuple[float, float],
) -> str:
    perfil = lector.perfil(handle)
    pausar(rng, dormir, pausa)
    embed = lector.embed(handle)
    fuente = f"{FUENTE}, leído el {hoy()}"
    crudo = {
        "perfil": perfil.crudo,
        "perfil_error": perfil.error,
        "embed_error": embed.error,
        "piezas": [
            {
                "id": pieza.item_id,
                "plays": pieza.plays,
                "slideshow": pieza.slideshow,
                "fijada": pieza.fijada,
                "created": pieza.created,
            }
            for pieza in embed.piezas
        ],
    }
    if perfil.error.startswith("status "):
        lector.anotar(vacio=False)
        guardar_cuenta(
            db,
            _base(
                handle,
                perfil,
                fuente,
                semilla,
                crudo,
                estado="descartada",
                motivo=f"sin datos accesibles ({perfil.error})",
            ),
            conservar_manual=conservar_manual,
        )
        return "descartada"
    if not embed.piezas and (perfil.error or embed.error):
        # El perfil puede haber respondido y el embed no. No se convierte en
        # «cero publicaciones»: si ya había métricas, se quedan.
        lector.anotar(vacio=True)
        if _ya_tenia_metricas(db, handle):
            return "sin_cambios"
        guardar_cuenta(
            db,
            _base(handle, perfil, fuente, semilla, crudo, estado="error", motivo=perfil.error or embed.error),
            conservar_manual=conservar_manual,
        )
        return "error"
    lector.anotar(vacio=False)
    resultado = evaluar(
        embed.piezas,
        bio=perfil.bio,
        enlace=perfil.enlace,
        handle=handle,
        nombre=perfil.nombre,
        idioma_perfil=perfil.idioma,
        privada=perfil.privada,
        vistas_minimas=vistas_minimas,
    )
    datos = _base(handle, perfil, fuente, semilla, crudo, estado=resultado.estado, motivo=resultado.motivo)
    datos.update(
        {
            "posts_muestreados": resultado.posts_muestreados,
            "fijados": resultado.fijados,
            "mediana_vistas": resultado.mediana_vistas,
            "slideshows_muestra": resultado.slideshows_muestra,
            "es_cuenta_slideshow": resultado.es_cuenta_slideshow,
            "num_piezas_virales": resultado.num_piezas_virales,
            "num_virales_no_fijadas": resultado.num_virales_no_fijadas,
            "mejor_reciente_url": resultado.mejor_reciente_url,
            "mejor_reciente_vistas": resultado.mejor_reciente_vistas,
            "mejor_reciente_multiplo": resultado.mejor_reciente_multiplo,
            "mejor_pieza_url": resultado.mejor_pieza_url,
            "mejor_pieza_vistas": resultado.mejor_pieza_vistas,
            "mejor_pieza_multiplo": resultado.mejor_pieza_multiplo,
            "mejor_pieza_slideshow": resultado.mejor_pieza_slideshow,
            "mejor_pieza_fijada": resultado.mejor_pieza_fijada,
            "piezas_virales": resultado.piezas_virales,
            "ultima_publicacion": resultado.ultima_publicacion,
            "captions": resultado.captions,
            "tipo_producto": resultado.tipo_producto,
            "idioma": resultado.idioma,
        }
    )
    existente = obtener_cuenta(db, handle)
    manual = bool(
        conservar_manual
        and existente
        and "revisada a mano" in (existente.get("clasificacion") or "")
    )
    if resultado.estado == "califica" and clasificador is not None and not manual:
        etiqueta = clasificador.clasificar(
            handle=handle,
            bio=perfil.bio,
            enlace=perfil.enlace,
            pies="\n".join(resultado.captions),
            tipo_producto=resultado.tipo_producto,
            idioma=resultado.idioma,
        )
        datos.update(
            {
                "nicho": etiqueta.nicho,
                "subnicho": etiqueta.subnicho,
                "que_vende": etiqueta.que_vende,
                "tipo_producto": etiqueta.tipo_producto or resultado.tipo_producto,
                "idioma": etiqueta.idioma or resultado.idioma,
                "clasificacion": etiqueta.clasificacion,
            }
        )
    guardar_cuenta(db, datos, conservar_manual=conservar_manual)
    if resultado.estado == "califica":
        texto = "\n".join([perfil.bio, perfil.enlace, *resultado.captions])
        conocidas = handles_conocidos(db)
        for mencionado in menciones(texto):
            encolar_handle(db, mencionado, f"mencion:@{handle}", conocidas)
        for tag in hashtags(texto)[:8]:
            anadir_semilla(db, "hashtag", tag, notas=f"visto en @{handle}")
    return resultado.estado


def _ya_tenia_metricas(db: Database, handle: str) -> bool:
    cuenta = obtener_cuenta(db, handle)
    if cuenta is None:
        return False
    return cuenta.get("estado") == "califica" or cuenta.get("mediana_vistas") is not None


def _base(handle, perfil, fuente, semilla, crudo, *, estado: str, motivo: str) -> dict:
    bio = perfil.bio.replace("\n", " | ") if perfil.bio else ""
    return {
        "handle": handle,
        "url": f"https://www.tiktok.com/@{handle}",
        "nombre": perfil.nombre,
        "bio": bio,
        "enlace_bio": perfil.enlace,
        "seguidores": perfil.seguidores,
        "fuente": fuente,
        "semilla": semilla,
        "estado": estado,
        "motivo": motivo,
        "fecha_actualizada": hoy(),
        "raw_json": json.dumps(crudo, ensure_ascii=False),
    }


def _paginas(
    db: Database,
    lector: Lector,
    conocidas: set[str],
    *,
    max_paginas: int,
    rng: random.Random,
    dormir,
    pausa: tuple[float, float],
) -> None:
    if max_paginas <= 0:
        return
    hechas = 0
    # Las listas públicas van antes que un hashtag genérico, y lo ya leído
    # hoy cede el turno: así una pasada corta no se queda siempre en lo mismo.
    prioridad = {"url": 0, "keyword": 1, "hashtag": 2}
    semillas = sorted(
        listar_semillas(db),
        key=lambda semilla: (semilla.get("leida_en") or "", prioridad.get(semilla["tipo"], 9), semilla["valor"]),
    )
    for semilla in semillas:
        if hechas >= max_paginas:
            return
        tipo = semilla["tipo"]
        valor = semilla["valor"]
        if tipo == "url":
            url, origen = valor, f"url:{valor}"
        elif tipo == "hashtag":
            url, origen = url_hashtag(valor), f"hashtag:{valor}"
        elif tipo == "keyword":
            slugs = slugs_de_keyword(valor)
            if not slugs:
                continue
            url, origen = url_hashtag(slugs[0]), f"keyword:{valor}"
        else:
            continue
        encontrados = lector.handles_de_pagina(url)
        marcar_semilla_leida(db, tipo, valor, hoy())
        for handle in encontrados:
            encolar_handle(db, handle, origen, conocidas)
        hechas += 1
        pausar(rng, dormir, pausa)
    if hechas >= max_paginas:
        return
    for cuenta in listar_cuentas(db, estado="califica"):
        texto = " ".join(
            [cuenta.get("bio") or "", cuenta.get("enlace_bio") or "", cuenta.get("que_vende") or ""]
        )
        slugs = slugs_de_texto(texto)
        if not slugs:
            continue
        encontrados = lector.handles_de_pagina(url_hashtag(slugs[0]))
        for handle in encontrados:
            encolar_handle(db, handle, f"hashtag:{slugs[0]}", conocidas)
        pausar(rng, dormir, pausa)
        return


class _Corte(TikTokBloqueado):
    def __init__(self, original: TikTokBloqueado, cifras: tuple[int, int, int, int]) -> None:
        super().__init__(str(original))
        self.original = original
        self.cifras = cifras


def _consumir(
    db: Database,
    lector: Lector,
    cuentas: list[dict],
    *,
    clasificador: Clasificador | None,
    vistas_minimas: int,
    conservar_manual: bool,
    rng: random.Random,
    dormir,
    pausa: tuple[float, float],
) -> tuple[int, int, int, int]:
    leidas = nuevas = califican = descartadas = 0
    for cuenta in cuentas:
        try:
            pausar(rng, dormir, pausa)
            antes = cuenta.get("estado")
            estado = _leer_y_guardar(
                db,
                lector,
                cuenta["handle"],
                semilla=cuenta.get("semilla") or "",
                clasificador=clasificador,
                vistas_minimas=vistas_minimas,
                conservar_manual=conservar_manual,
                rng=rng,
                dormir=dormir,
                pausa=pausa,
            )
        except TikTokBloqueado as exc:
            raise _Corte(exc, (leidas, nuevas, califican, descartadas)) from exc
        if estado == "sin_cambios":
            continue
        leidas += 1
        if antes in {"candidata", "error", None, ""}:
            nuevas += 1
        if estado == "califica":
            califican += 1
        elif estado == "descartada":
            descartadas += 1
    return leidas, nuevas, califican, descartadas


def ejecutar_descubrimiento(
    db: Database,
    settings,
    *,
    transporte: Transporte | None = None,
    permitir_red: bool = False,
    max_cuentas: int | None = None,
    max_paginas: int | None = None,
    clasificador: Clasificador | None = None,
    sin_llm: bool = False,
    usar_apify: bool = False,
    apify_buscar=None,
    rng: random.Random | None = None,
    dormir=None,
    pausa: tuple[float, float] | None = None,
) -> Informe:
    inicio = utc_now()
    asegurar_semillas_defecto(db)
    limite = max_cuentas if max_cuentas is not None else settings.tt_max_cuentas
    paginas = max_paginas if max_paginas is not None else settings.tt_max_paginas
    if not permitir_red:
        conocidas = handles_conocidos(db)
        locales = bola_de_nieve_local(db, conocidas)
        cola = len(pendientes(db, ("candidata", "error"), max(limite, 1) * 50))
        return _cerrar(
            db,
            tipo="descubrir",
            inicio=inicio,
            informe=Informe(
                status="plan",
                mensaje=(
                    f"Hay {cola} candidatas en cola"
                    + (f" ({locales} nuevas por menciones ya leídas)" if locales else "")
                    + f". En la siguiente pasada se leerían hasta {limite} cuentas y {paginas} páginas. "
                    "Añade --yes para leer TikTok. No se ha hecho ninguna petición."
                ),
                candidatas=cola,
            ),
        )

    propio_transporte = transporte is None
    if transporte is None:
        from radar.tiktok.fuente import HttpxTransporte

        transporte = HttpxTransporte()
    propio = clasificador is None
    if clasificador is None:
        from radar.tiktok.clasificar import clasificador_desde_ajustes

        clasificador = clasificador_desde_ajustes(settings, sin_llm=sin_llm)
    rng = rng or random.Random()
    dormir = dormir or _dormir
    tramo = pausa if pausa is not None else settings.tt_pause
    avisos: list[str] = []
    try:
        conocidas = handles_conocidos(db)
        if usar_apify:
            consultas = [semilla["valor"] for semilla in listar_semillas(db) if semilla["tipo"] == "keyword"]
            buscar = apify_buscar or buscar_handles
            resultado = buscar(settings.apify_token, settings.tt_apify_actor, consultas)
            if resultado.mensaje:
                avisos.append(resultado.mensaje)
            for handle in resultado.handles:
                encolar_handle(db, handle, f"apify:{settings.tt_apify_actor or 'actor'}", conocidas)
        bola_de_nieve_local(db, conocidas)
        lector = Lector(transporte)
        bloqueado = False
        texto_bloqueo = ""
        leidas = nuevas = califican = descartadas = 0
        try:
            _paginas(db, lector, conocidas, max_paginas=paginas, rng=rng, dormir=dormir, pausa=tramo)
            cola = pendientes(db, ("candidata", "error"), limite)
            leidas, nuevas, califican, descartadas = _consumir(
                db,
                lector,
                cola,
                clasificador=clasificador,
                vistas_minimas=settings.tt_vistas_min,
                conservar_manual=True,
                rng=rng,
                dormir=dormir,
                pausa=tramo,
            )
        except TikTokBloqueado as exc:
            bloqueado = True
            texto_bloqueo = str(exc)
            if isinstance(exc, _Corte):
                leidas, nuevas, califican, descartadas = exc.cifras
        restantes = contar_por_estado(db).get("candidata", 0)
        if bloqueado:
            mensaje = texto_bloqueo
            status = "bloqueado"
        else:
            mensaje = (
                f"Leídas {leidas}. Nuevas {nuevas}. Califican {califican}. "
                f"Descartadas {descartadas}. Candidatas en cola: {restantes}."
            )
            status = "ok"
        if avisos:
            mensaje = " ".join([*avisos, mensaje])
        return _cerrar(
            db,
            tipo="descubrir",
            inicio=inicio,
            informe=Informe(
                status=status,
                mensaje=mensaje.strip(),
                leidas=leidas,
                nuevas=nuevas,
                califican=califican,
                descartadas=descartadas,
                candidatas=restantes,
                bloqueado=bloqueado,
                avisos=avisos,
            ),
        )
    finally:
        if propio and clasificador is not None:
            clasificador.close()
        if propio_transporte:
            cerrar = getattr(transporte, "close", None)
            if cerrar:
                cerrar()


def ejecutar_actualizacion(
    db: Database,
    settings,
    *,
    transporte: Transporte | None = None,
    permitir_red: bool = False,
    max_cuentas: int | None = None,
    handle: str | None = None,
    clasificador: Clasificador | None = None,
    sin_llm: bool = False,
    rng: random.Random | None = None,
    dormir=None,
    pausa: tuple[float, float] | None = None,
) -> Informe:
    inicio = utc_now()
    limite = max_cuentas if max_cuentas is not None else settings.tt_max_cuentas
    if handle:
        cuenta = obtener_cuenta(db, handle)
        if cuenta is None:
            return Informe(status="error", mensaje=f"No está @{normalizar_handle(handle) or handle}.")
        cola = [cuenta]
    else:
        cola = pendientes(db, ("califica",), limite)
    if not permitir_red:
        return _cerrar(
            db,
            tipo="actualizar",
            inicio=inicio,
            informe=Informe(
                status="plan",
                mensaje=(
                    f"Se actualizarían {len(cola)} cuentas, las de lectura más antigua primero. "
                    "Añade --yes para leer TikTok. No se ha hecho ninguna petición."
                ),
                candidatas=len(cola),
            ),
        )
    propio_transporte = transporte is None
    if transporte is None:
        from radar.tiktok.fuente import HttpxTransporte

        transporte = HttpxTransporte()
    propio = clasificador is None
    if clasificador is None:
        from radar.tiktok.clasificar import clasificador_desde_ajustes

        clasificador = clasificador_desde_ajustes(settings, sin_llm=sin_llm)
    rng = rng or random.Random()
    dormir = dormir or _dormir
    tramo = pausa if pausa is not None else settings.tt_pause
    leidas = nuevas = califican = descartadas = 0
    try:
        lector = Lector(transporte)
        try:
            leidas, nuevas, califican, descartadas = _consumir(
                db,
                lector,
                cola,
                clasificador=clasificador,
                vistas_minimas=settings.tt_vistas_min,
                conservar_manual=True,
                rng=rng,
                dormir=dormir,
                pausa=tramo,
            )
            bloqueado = False
            mensaje = (
                f"Actualizadas {leidas}. Siguen calificando {califican}. "
                f"Pasan a descartadas {descartadas}."
            )
            status = "ok"
        except TikTokBloqueado as exc:
            bloqueado = True
            mensaje = str(exc)
            status = "bloqueado"
            if isinstance(exc, _Corte):
                leidas, nuevas, califican, descartadas = exc.cifras
        return _cerrar(
            db,
            tipo="actualizar",
            inicio=inicio,
            informe=Informe(
                status=status,
                mensaje=mensaje,
                leidas=leidas,
                nuevas=nuevas,
                califican=califican,
                descartadas=descartadas,
                bloqueado=bloqueado,
            ),
        )
    finally:
        if propio and clasificador is not None:
            clasificador.close()
        if propio_transporte:
            cerrar = getattr(transporte, "close", None)
            if cerrar:
                cerrar()


def ejecutar_ciclo(db: Database, settings, **kwargs) -> Informe:
    descubrimiento = ejecutar_descubrimiento(db, settings, **kwargs)
    if not kwargs.get("permitir_red") or descubrimiento.bloqueado:
        if descubrimiento.bloqueado:
            descubrimiento.mensaje += " No se actualizan las cuentas ya guardadas en esta pasada."
        return descubrimiento
    resto = {
        clave: valor
        for clave, valor in kwargs.items()
        if clave not in {"usar_apify", "apify_buscar", "max_paginas"}
    }
    actualizacion = ejecutar_actualizacion(db, settings, **resto)
    return Informe(
        status=actualizacion.status,
        mensaje=f"Descubrimiento: {descubrimiento.mensaje} Actualización: {actualizacion.mensaje}",
        leidas=descubrimiento.leidas + actualizacion.leidas,
        nuevas=descubrimiento.nuevas + actualizacion.nuevas,
        califican=descubrimiento.califican + actualizacion.califican,
        descartadas=descubrimiento.descartadas + actualizacion.descartadas,
        candidatas=descubrimiento.candidatas,
        bloqueado=actualizacion.bloqueado,
    )


def _cerrar(db: Database, *, tipo: str, inicio: str, informe: Informe) -> Informe:
    if informe.status != "plan":
        registrar_pasada(
            db,
            tipo=tipo,
            started_at=inicio,
            finished_at=utc_now(),
            status=informe.status,
            message=informe.mensaje,
            leidas=informe.leidas,
            nuevas=informe.nuevas,
            califican=informe.califican,
            descartadas=informe.descartadas,
        )
    return informe


def _dormir(segundos: float) -> None:
    import time

    time.sleep(segundos)
