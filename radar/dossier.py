"""Siete bloques de la ficha, armados solo con datos guardados.

No se inventan métricas ni testimonios. Lo que falte queda en [X]. El
desglose usa las marcas de tiempo de la transcripción o, en un carrusel,
el orden de las diapositivas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from radar.carrusel import plantilla_carrusel, rol_diapositiva
from radar.db import BankEntry
from radar.perfil import Perfil


@dataclass
class Dossier:
    gancho: str
    receta: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    ajustes: list[str] = field(default_factory=list)
    ideas: list[str] = field(default_factory=list)
    monetizacion: str = ""
    adaptacion: str = ""
    desglose: list[str] = field(default_factory=list)
    desglose_nota: str = ""
    plantilla: str = ""


def construir_dossier(entry: BankEntry, perfil: Perfil | None = None) -> Dossier:
    perfil = perfil or Perfil()
    slides = list(entry.slides or [])
    plantilla = (entry.template or "").strip() or _plantilla(entry, slides)
    return Dossier(
        gancho=_gancho(entry, slides),
        receta=_receta(entry, slides),
        prompts=_prompts(perfil),
        ajustes=_ajustes(entry, slides),
        ideas=_ideas(entry, slides),
        monetizacion=_monetizacion(perfil),
        adaptacion=_adaptacion(entry, perfil, plantilla),
        desglose=_desglose(entry, slides)[0],
        desglose_nota=_desglose(entry, slides)[1],
        plantilla=plantilla or "[X]",
    )


def _gancho(entry: BankEntry, slides: list[dict]) -> str:
    texto = (entry.hook or "").strip()
    if not texto and slides:
        texto = str(slides[0].get("texto") or "").strip()
    if not texto:
        texto = "[X]"
    tipo = entry.hook_type or entry.categoria or "sin clasificar"
    return f"{texto}\nTipo: {tipo}."


def _receta(entry: BankEntry, slides: list[dict]) -> list[str]:
    if slides:
        total = len(slides)
        lineas = []
        for slide in slides:
            indice = int(slide.get("indice") or 0)
            rol = rol_diapositiva(indice, total)
            texto = str(slide.get("texto") or "").strip() or "[sin texto en la diapositiva]"
            lineas.append(f"Diapositiva {indice} ({rol}): {texto}")
        return lineas
    if entry.blocks:
        return [
            f"{bloque.get('id', '')}: {bloque.get('texto') or '[X]'} · en pantalla: {bloque.get('en_pantalla') or '[X]'}"
            for bloque in entry.blocks
        ]
    return ["Pendiente de analizar. No se rellena la receta."]


def _prompts(perfil: Perfil) -> list[str]:
    return [
        (
            "Rellena la plantilla de este formato con la oferta del perfil. "
            f"Oferta: {perfil.hueco('oferta')}. CTA: {perfil.hueco('cta')}. "
            "Si un dato no está en el material ni en el perfil, escribe [X]. No añadas cifras."
        ),
        (
            "Escribe el guion hablado, de tú, con los mismos bloques y en el mismo orden. "
            f"Pruebas que se pueden citar: {perfil.hueco('pruebas')}. "
            "La anécdota que no esté escrita queda en [X]."
        ),
    ]


def _ajustes(entry: BankEntry, slides: list[dict]) -> list[str]:
    if slides:
        return [
            "Una diapositiva por bloque. El gancho es la primera y el cierre la última.",
            "No se anota música, tipografía ni duración: el carrusel no trae esos datos.",
        ]
    vistos = []
    for bloque in entry.blocks:
        pantalla = str(bloque.get("en_pantalla") or "").strip()
        if pantalla and pantalla != "[X]":
            vistos.append(f"{bloque.get('id', '')}: {pantalla}")
    if vistos:
        return vistos
    return ["El material no describe el montaje. Anótalo como [X]."]


def _ideas(entry: BankEntry, slides: list[dict]) -> list[str]:
    if not entry.blocks and not slides and not entry.template:
        return ["Sin formato clasificado todavía. No se derivan ideas."]
    return [
        "Misma receta: pasa el conflicto al primer bloque y deja la anécdota en [X].",
        "Misma receta en diapositivas: un bloque por diapositiva y el cierre con el CTA del perfil, o [X] si no hay CTA.",
    ]


def _monetizacion(perfil: Perfil) -> str:
    return (
        "Hipótesis de encaje, no un resultado medido. "
        f"Oferta: {perfil.hueco('oferta')}. CTA: {perfil.hueco('cta')}. "
        f"Audiencia: {perfil.hueco('audiencia')}. "
        "No hay ventas, leads ni ingresos en el material: si hicieran falta, [X]."
    )


def _adaptacion(entry: BankEntry, perfil: Perfil, plantilla: str) -> str:
    cuerpo = (entry.adapted_script or "").strip() or plantilla or "[X]"
    return (
        f"Nicho: {perfil.hueco('nicho')}. Tono: {perfil.hueco('tono')}. "
        f"Idioma: {perfil.hueco('idioma')}. Restricciones: {perfil.hueco('restricciones')}.\n\n"
        f"{cuerpo}"
    )


def _plantilla(entry: BankEntry, slides: list[dict]) -> str:
    if slides:
        return plantilla_carrusel(slides)
    if entry.blocks:
        return "\n".join(
            f"{str(bloque.get('id') or '').capitalize()}: {bloque.get('texto') or '[X]'}" for bloque in entry.blocks
        )
    return ""


def _desglose(entry: BankEntry, slides: list[dict]) -> tuple[list[str], str]:
    segmentos = []
    for segmento in entry.segments or []:
        if not isinstance(segmento, dict):
            continue
        texto = str(segmento.get("text") or segmento.get("texto") or "").strip()
        if not texto:
            continue
        inicio = segmento.get("start", segmento.get("inicio"))
        fin = segmento.get("end", segmento.get("fin"))
        if inicio is None or fin is None:
            continue
        segmentos.append(f"{_segundo(inicio)}–{_segundo(fin)} s · {texto}")
    if segmentos:
        return segmentos, "Tiempos copiados de los segmentos de la transcripción."
    if slides:
        lineas = []
        for slide in slides:
            texto = str(slide.get("texto") or "").strip() or "[sin texto]"
            lineas.append(f"Diapositiva {slide.get('indice')} · {texto}")
        return lineas, "No hay audio: el desglose sigue el orden de las diapositivas, sin minutado inventado."
    return [], "La transcripción no trae marcas de tiempo. No se inventa un desglose."


def _segundo(valor) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "[X]"
    return f"{numero:.1f}".replace(".", ",")
