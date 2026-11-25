"""Descubre creadores y refresca los que ya están, sin inventar cifras."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from radar.db import Database, utc_now
from radar.instagram.apify_opcional import buscar_handles
from radar.instagram.clasificar import Clasificador
from radar.instagram.fuente import (
    InstagramBloqueado,
    Sesion,
    pausar,
    url_de_semilla,
)
from radar.instagram.parse import Lectura
from radar.instagram.reglas import evaluar, menciones, normalizar_handle
from radar.instagram.store import (
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

FUENTE = (
    "Instagram (pestaña Reels con la sesión del navegador del motor, "
    "Playwright, pausas 25-50 s)"
)


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
        texto = "\n".join([cuenta.get("bio") or "", cuenta.get("enlace") or "", pies])
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
    sesion: Sesion,
    handle: str,
    *,
    semilla: str,
    clasificador: Clasificador | None,
    vistas_minimas: int,
    recientes: int,
    conservar_manual: bool,
) -> str:
    lectura = sesion.leer_perfil(handle)
    fuente = f"{FUENTE}, leído el {hoy()}"
    if lectura.error and not lectura.reels and not lectura.privada:
        if _ya_tenia_metricas(db, handle):
            return "sin_cambios"
        guardar_cuenta(
            db,
            _base(handle, lectura, fuente, semilla, estado="error", motivo=lectura.error or "sin ficha"),
            conservar_manual=conservar_manual,
        )
        return "error"
    resultado = evaluar(
        lectura.reels,
        bio=lectura.bio,
        handle=handle,
        privada=lectura.privada,
        vistas_minimas=vistas_minimas,
        recientes=recientes,
    )
    existente = obtener_cuenta(db, handle)
    manual = bool(
        conservar_manual and existente and "revisada a mano" in (existente.get("clasificacion") or "")
    )
    estado, motivo, etiqueta = _decidir(resultado, lectura, clasificador, manual=manual)
    datos = _base(handle, lectura, fuente, semilla, estado=estado, motivo=motivo)
    if not lectura.privada and lectura.reels:
        datos.update(
            {
                "reels_muestreados": resultado.reels_muestreados,
                "fijados": resultado.fijados,
                "mediana_vistas": resultado.mediana_vistas,
                "num_piezas_virales": resultado.num_piezas_virales,
                "num_virales_no_fijadas": resultado.num_virales_no_fijadas,
                "mejor_pieza_url": resultado.mejor_pieza_url,
                "mejor_pieza_vistas": resultado.mejor_pieza_vistas,
                "mejor_pieza_multiplo": resultado.mejor_pieza_multiplo,
                "mejor_pieza_fijada": resultado.mejor_pieza_fijada,
                "piezas_virales": resultado.piezas_virales,
                "captions": resultado.captions,
                "idioma": (etiqueta.idioma if etiqueta and etiqueta.idioma else resultado.idioma),
            }
        )
    if etiqueta is not None and not manual:
        datos.update(
            {
                "nicho": etiqueta.nicho if etiqueta.en_nicho else "",
                "subnicho": etiqueta.subnicho if etiqueta.en_nicho else "",
                "idioma": etiqueta.idioma or datos.get("idioma") or "",
                "clasificacion": etiqueta.clasificacion,
            }
        )
    if lectura.verificado is not None:
        datos["verificado"] = "sí" if lectura.verificado else "no"
    guardar_cuenta(db, datos, conservar_manual=conservar_manual)
    if estado == "califica":
        _encolar_relacionados(db, handle, lectura, resultado.captions)
    return estado


def _decidir(resultado, lectura: Lectura, clasificador: Clasificador | None, *, manual: bool):
    if lectura.privada or not resultado.tiene_viral:
        return "descartada", resultado.motivo, None
    if manual:
        return "califica", "", None
    if clasificador is None:
        return "descartada", "fuera de nicho", None
    etiqueta = clasificador.clasificar(
        handle=lectura.handle,
        nombre=lectura.nombre,
        bio=lectura.bio,
        enlace=lectura.enlace,
        pies="\n".join(resultado.captions),
        idioma=resultado.idioma,
    )
    if etiqueta.en_nicho:
        return "califica", "", etiqueta
    return "descartada", "fuera de nicho", etiqueta


def _encolar_relacionados(db: Database, handle: str, lectura: Lectura, captions: list[str]) -> None:
    conocidas = handles_conocidos(db)
    texto = "\n".join([lectura.bio, lectura.enlace, *captions])
    for mencionado in menciones(texto):
        encolar_handle(db, mencionado, f"mencion:@{handle}", conocidas)
    for sugerido in lectura.sugeridos:
        encolar_handle(db, sugerido, f"sugerida:@{handle}", conocidas)
    for colab in lectura.colaboradores:
        encolar_handle(db, colab, f"colab:@{handle}", conocidas)


def _ya_tenia_metricas(db: Database, handle: str) -> bool:
    cuenta = obtener_cuenta(db, handle)
    if cuenta is None:
        return False
    return cuenta.get("estado") == "califica" or cuenta.get("mediana_vistas") is not None


def _base(handle, lectura: Lectura, fuente, semilla, *, estado: str, motivo: str) -> dict:
    bio = lectura.bio.replace("\n", " | ") if lectura.bio else ""
    datos = {
        "handle": handle,
        "url": f"https://www.instagram.com/{handle}/",
        "fuente": fuente,
        "semilla": semilla,
        "estado": estado,
        "motivo": motivo,
        "fecha_actualizada": hoy(),
        "raw_json": json.dumps(lectura.crudo, ensure_ascii=False) if lectura.crudo else "",
    }
    if lectura.nombre:
        datos["nombre"] = lectura.nombre
    if bio:
        datos["bio"] = bio
    if lectura.enlace:
        datos["enlace"] = lectura.enlace
    if lectura.seguidores is not None:
        datos["seguidores"] = lectura.seguidores
    return datos


def _paginas(
    db: Database,
    sesion: Sesion,
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
    prioridad = {"url": 0, "keyword": 1, "hashtag": 2}
    semillas = sorted(
        listar_semillas(db),
        key=lambda semilla: (semilla.get("leida_en") or "", prioridad.get(semilla["tipo"], 9), semilla["valor"]),
    )
    for semilla in semillas:
        if hechas >= max_paginas:
            return
        tipo = semilla["tipo"]
        if tipo == "handle":
            continue
        url = url_de_semilla(tipo, semilla["valor"])
        if not url:
            continue
        if hechas:
            pausar(rng, dormir, pausa)
        encontrados = sesion.handles_de_pagina(url)
        marcar_semilla_leida(db, tipo, semilla["valor"], hoy())
        origen = f"{tipo}:{semilla['valor']}"
        for handle in encontrados:
            encolar_handle(db, handle, origen, conocidas)
        hechas += 1


class _Corte(InstagramBloqueado):
    def __init__(self, original: InstagramBloqueado, cifras: tuple[int, int, int, int]) -> None:
        super().__init__(str(original))
        self.original = original
        self.cifras = cifras


def _consumir(
    db: Database,
    sesion: Sesion,
    cuentas: list[dict],
    *,
    clasificador: Clasificador | None,
    vistas_minimas: int,
    recientes: int,
    conservar_manual: bool,
    rng: random.Random,
    dormir,
    pausa: tuple[float, float],
) -> tuple[int, int, int, int]:
    leidas = nuevas = califican = descartadas = 0
    for indice, cuenta in enumerate(cuentas):
        if indice:
            pausar(rng, dormir, pausa)
        try:
            antes = cuenta.get("estado")
            estado = _leer_y_guardar(
                db,
                sesion,
                cuenta["handle"],
                semilla=cuenta.get("semilla") or "",
                clasificador=clasificador,
                vistas_minimas=vistas_minimas,
                recientes=recientes,
                conservar_manual=conservar_manual,
            )
        except InstagramBloqueado as exc:
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
    sesion: Sesion | None = None,
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
    limite = max_cuentas if max_cuentas is not None else settings.ig_creadores_max
    paginas = max_paginas if max_paginas is not None else settings.ig_creadores_max_paginas
    if not permitir_red:
        conocidas = handles_conocidos(db)
        locales = bola_de_nieve_local(db, conocidas)
        cola = len(pendientes(db, ("candidata", "error"), max(limite, 1) * 50))
        bajo, alto = settings.ig_creadores_pause
        return _cerrar(
            db,
            tipo="descubrir",
            inicio=inicio,
            informe=Informe(
                status="plan",
                mensaje=(
                    f"Hay {cola} candidatas en cola"
                    + (f" ({locales} nuevas por menciones ya leídas)" if locales else "")
                    + f". En la siguiente pasada se leerían hasta {limite} perfiles y {paginas} páginas, "
                    f"con pausas de {bajo:g}-{alto:g} s. "
                    "Añade --yes para leer Instagram. No se ha hecho ninguna petición."
                ),
                candidatas=cola,
            ),
        )

    avisos: list[str] = []
    conocidas = handles_conocidos(db)
    if usar_apify:
        consultas = [semilla["valor"] for semilla in listar_semillas(db) if semilla["tipo"] in {"keyword", "hashtag"}]
        buscar = apify_buscar or buscar_handles
        resultado = buscar(settings.apify_token, settings.ig_creadores_apify_actor, consultas)
        if resultado.mensaje:
            avisos.append(resultado.mensaje)
        for handle in resultado.handles:
            encolar_handle(db, handle, f"apify:{settings.ig_creadores_apify_actor or 'actor'}", conocidas)
    bola_de_nieve_local(db, conocidas)

    propio = clasificador is None
    propia_sesion = sesion is None
    rng = rng or random.Random()
    dormir = dormir or _dormir
    tramo = pausa if pausa is not None else settings.ig_creadores_pause
    leidas = nuevas = califican = descartadas = 0
    try:
        if propio:
            from radar.instagram.clasificar import clasificador_desde_ajustes

            clasificador = clasificador_desde_ajustes(settings, sin_llm=sin_llm)
        if propia_sesion:
            from radar.instagram.fuente import abrir_sesion

            sesion = abrir_sesion(settings)
        bloqueado = False
        texto_bloqueo = ""
        try:
            _paginas(db, sesion, conocidas, max_paginas=paginas, rng=rng, dormir=dormir, pausa=tramo)
            if paginas > 0:
                pausar(rng, dormir, tramo)
            cola = pendientes(db, ("candidata", "error"), limite)
            leidas, nuevas, califican, descartadas = _consumir(
                db,
                sesion,
                cola,
                clasificador=clasificador,
                vistas_minimas=settings.ig_creadores_vistas_min,
                recientes=settings.ig_creadores_reels,
                conservar_manual=True,
                rng=rng,
                dormir=dormir,
                pausa=tramo,
            )
        except InstagramBloqueado as exc:
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
        if propia_sesion and sesion is not None:
            sesion.close()


def ejecutar_actualizacion(
    db: Database,
    settings,
    *,
    sesion: Sesion | None = None,
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
    limite = max_cuentas if max_cuentas is not None else settings.ig_creadores_max
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
                    f"Se actualizarían {len(cola)} perfiles, los de lectura más antigua primero. "
                    "Añade --yes para leer Instagram. No se ha hecho ninguna petición."
                ),
                candidatas=len(cola),
            ),
        )
    propio = clasificador is None
    propia_sesion = sesion is None
    rng = rng or random.Random()
    dormir = dormir or _dormir
    tramo = pausa if pausa is not None else settings.ig_creadores_pause
    leidas = nuevas = califican = descartadas = 0
    try:
        if propio:
            from radar.instagram.clasificar import clasificador_desde_ajustes

            clasificador = clasificador_desde_ajustes(settings, sin_llm=sin_llm)
        if propia_sesion:
            from radar.instagram.fuente import abrir_sesion

            sesion = abrir_sesion(settings)
        try:
            leidas, nuevas, califican, descartadas = _consumir(
                db,
                sesion,
                cola,
                clasificador=clasificador,
                vistas_minimas=settings.ig_creadores_vistas_min,
                recientes=settings.ig_creadores_reels,
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
        except InstagramBloqueado as exc:
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
        if propia_sesion and sesion is not None:
            sesion.close()


def ejecutar_ciclo(db: Database, settings, **kwargs) -> Informe:
    descubrimiento = ejecutar_descubrimiento(db, settings, **kwargs)
    if not kwargs.get("permitir_red") or descubrimiento.bloqueado:
        if descubrimiento.bloqueado:
            descubrimiento.mensaje += " No se actualizan los creadores ya guardados en esta pasada."
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
