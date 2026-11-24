"""Categorías propias de la biblioteca.

Salen del tipo de gancho que ya clasifica el análisis. No son las de ningún
otro producto: aquí nombran el mecanismo que Antonio puede reutilizar.
El formato (reel o carrusel) es un filtro aparte.
"""

from __future__ import annotations

HOOK_A_CATEGORIA = {
    "pregunta": "pregunta de apertura",
    "afirmación contraintuitiva": "choque de expectativa",
    "lista": "lista o pasos",
    "historia personal": "relato en primera persona",
    "demostración": "muestra de herramienta",
    "error común": "error y corrección",
    "comparación": "contraste",
    "noticia": "novedad",
    "otro": "sin clasificar",
}

CATEGORIAS = (
    "pregunta de apertura",
    "choque de expectativa",
    "lista o pasos",
    "relato en primera persona",
    "muestra de herramienta",
    "error y corrección",
    "contraste",
    "novedad",
    "lectura por diapositivas",
    "sin clasificar",
)


def categoria_de(hook_type: str | None, media_kind: str | None = None) -> str:
    if (media_kind or "reel") == "carrusel" and not hook_type:
        return "lectura por diapositivas"
    if hook_type in HOOK_A_CATEGORIA:
        return HOOK_A_CATEGORIA[hook_type]
    if (media_kind or "reel") == "carrusel":
        return "lectura por diapositivas"
    return "sin clasificar"
