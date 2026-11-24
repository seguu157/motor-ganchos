"""Flujo Oferta → Referencias → Hooks → Guion → Variantes → Revisión → Exportación.

Las variantes no inventan un formato: cambian el cierre de la plantilla que
ya salió de un outlier. Un ejemplo no puede ser el gancho de trabajo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from radar.db import Database
from radar.perfil import CAMPOS, Perfil, perfil_desde_dict

PASOS = (
    "oferta",
    "referencias",
    "hooks",
    "guion",
    "variantes",
    "revision",
    "exportacion",
)

ETIQUETAS = {
    "oferta": "Oferta",
    "referencias": "Referencias",
    "hooks": "Hooks",
    "guion": "Guion",
    "variantes": "Variantes",
    "revision": "Revisión",
    "exportacion": "Exportación",
}

ESTADOS = ("borrador", "en_revision", "aprobado", "exportado")


class FlujoError(ValueError):
    """El paso no se puede guardar con estos datos."""


@dataclass
class Proyecto:
    id: int
    titulo: str
    estado: str
    paso: str
    perfil: Perfil
    referencias: list[int] = field(default_factory=list)
    hook_reel_id: int | None = None
    guion: str = ""
    variantes: list[dict[str, str]] = field(default_factory=list)
    notas: str = ""


def crear_proyecto(db: Database, titulo: str) -> Proyecto:
    limpio = (titulo or "").strip()
    if not limpio:
        raise FlujoError("El proyecto necesita un título.")
    proyecto_id = db.create_proyecto(limpio, perfil_json=json.dumps(db.get_perfil().as_dict(), ensure_ascii=False))
    proyecto = cargar_proyecto(db, proyecto_id)
    if proyecto is None:
        raise FlujoError("No se pudo crear el proyecto.")
    return proyecto


def cargar_proyecto(db: Database, proyecto_id: int) -> Proyecto | None:
    row = db.get_proyecto(proyecto_id)
    if row is None:
        return None
    perfil = perfil_desde_dict(json.loads(row["perfil_json"] or "{}"))
    referencias = [int(item) for item in json.loads(row["referencias_json"] or "[]")]
    variantes = json.loads(row["variantes_json"] or "[]")
    return Proyecto(
        id=int(row["id"]),
        titulo=row["titulo"],
        estado=row["estado"],
        paso=row["paso"],
        perfil=perfil,
        referencias=referencias,
        hook_reel_id=row["hook_reel_id"],
        guion=row["guion"] or "",
        variantes=list(variantes),
        notas=row["notas"] or "",
    )


def listar_proyectos(db: Database) -> list[Proyecto]:
    return [proyecto for row in db.list_proyectos() if (proyecto := cargar_proyecto(db, int(row["id"]))) is not None]


def aplicar_oferta(db: Database, proyecto_id: int, perfil: Perfil) -> Proyecto:
    _exigir(db, proyecto_id)
    db.save_perfil(perfil)
    db.update_proyecto(
        proyecto_id,
        perfil_json=json.dumps(perfil.as_dict(), ensure_ascii=False),
        paso="referencias",
        estado="borrador",
    )
    return _exigir(db, proyecto_id)


def aplicar_referencias(db: Database, proyecto_id: int, reel_ids: list[int]) -> Proyecto:
    _exigir(db, proyecto_id)
    if not reel_ids:
        raise FlujoError("Elige al menos una referencia del banco.")
    vistos: list[int] = []
    for reel_id in reel_ids:
        if reel_id in vistos:
            continue
        _referencia_valida(db, reel_id)
        vistos.append(reel_id)
    db.update_proyecto(
        proyecto_id,
        referencias_json=json.dumps(vistos),
        hook_reel_id=None,
        paso="hooks",
        estado="borrador",
    )
    return _exigir(db, proyecto_id)


def aplicar_hook(db: Database, proyecto_id: int, reel_id: int) -> Proyecto:
    proyecto = _exigir(db, proyecto_id)
    if reel_id not in proyecto.referencias:
        raise FlujoError("El gancho tiene que ser una de las referencias elegidas.")
    _referencia_valida(db, reel_id)
    db.update_proyecto(proyecto_id, hook_reel_id=reel_id, paso="guion", estado="borrador")
    return _exigir(db, proyecto_id)


def aplicar_guion(db: Database, proyecto_id: int) -> Proyecto:
    proyecto = _exigir(db, proyecto_id)
    if proyecto.hook_reel_id is None:
        raise FlujoError("Elige antes el gancho.")
    entry = _referencia_valida(db, proyecto.hook_reel_id)
    base = (entry.adapted_script or entry.template or "").strip()
    if not base:
        raise FlujoError("Esa referencia no tiene plantilla ni guion. Analízala antes de seguir.")
    cta = proyecto.perfil.cta.strip() or "[X]"
    guion = _sin_cierre(base)
    guion = f"{guion}\nCTA: {cta}".strip()
    db.update_proyecto(proyecto_id, guion=guion, paso="variantes", estado="borrador")
    return _exigir(db, proyecto_id)


def aplicar_variantes(db: Database, proyecto_id: int) -> Proyecto:
    proyecto = _exigir(db, proyecto_id)
    if not proyecto.guion.strip():
        raise FlujoError("Genera el guion antes de las variantes.")
    variantes = variantes_de_guion(proyecto.guion, proyecto.perfil)
    db.update_proyecto(
        proyecto_id,
        variantes_json=json.dumps(variantes, ensure_ascii=False),
        paso="revision",
        estado="borrador",
    )
    return _exigir(db, proyecto_id)


def aplicar_revision(db: Database, proyecto_id: int, notas: str, *, aprobar: bool) -> Proyecto:
    proyecto = _exigir(db, proyecto_id)
    if not proyecto.variantes:
        raise FlujoError("Genera las variantes antes de revisar.")
    estado = "aprobado" if aprobar else "en_revision"
    db.update_proyecto(proyecto_id, notas=(notas or "").strip(), estado=estado, paso="exportacion")
    return _exigir(db, proyecto_id)


def exportar_proyecto(db: Database, proyecto_id: int) -> str:
    proyecto = _exigir(db, proyecto_id)
    if proyecto.estado not in {"en_revision", "aprobado", "exportado"}:
        raise FlujoError("Pasa antes por la revisión. No se exporta un borrador.")
    if proyecto.hook_reel_id is None:
        raise FlujoError("Falta el gancho de trabajo.")
    entry = _referencia_valida(db, proyecto.hook_reel_id)
    texto = render_proyecto(proyecto, entry)
    db.update_proyecto(proyecto_id, estado="exportado", paso="exportacion")
    return texto


def variantes_de_guion(guion: str, perfil: Perfil) -> list[dict[str, str]]:
    cuerpo = _sin_cierre(guion)
    cierres = (
        f"Cierre: {perfil.cta.strip() or '[X]'}",
        "Cierre: Si te pasa, prueba [X].",
        "Cierre: El siguiente paso es [X].",
    )
    return [{"id": str(indice), "texto": f"{cuerpo}\n{cierre}".strip()} for indice, cierre in enumerate(cierres, start=1)]


def render_proyecto(proyecto: Proyecto, entry) -> str:
    lineas = [
        f"# {proyecto.titulo}",
        "",
        f"Estado: {proyecto.estado}",
        "",
        "## Oferta",
        "",
    ]
    for campo in CAMPOS:
        lineas.append(f"- **{campo}:** {proyecto.perfil.hueco(campo)}")
    lineas.extend(
        [
            "",
            "## Referencia",
            "",
            f"- **Cuenta:** @{entry.account}",
            f"- **URL:** {entry.url}",
            f"- **Múltiplo:** {entry.multiple if entry.multiple is not None else '[X]'}",
            f"- **Ejemplo:** {'sí' if entry.is_example else 'no'}",
            "",
            "## Guion",
            "",
            proyecto.guion.strip() or "[X]",
            "",
            "## Variantes",
            "",
        ]
    )
    for variante in proyecto.variantes:
        lineas.append(f"### {variante.get('id', '')}")
        lineas.append("")
        lineas.append(str(variante.get("texto") or "[X]"))
        lineas.append("")
    lineas.extend(["## Revisión", "", proyecto.notas.strip() or "[X]", ""])
    return "\n".join(lineas).rstrip() + "\n"


def _sin_cierre(texto: str) -> str:
    lineas = []
    for linea in (texto or "").splitlines():
        prefijo = linea.strip().casefold()
        if prefijo.startswith("cierre:") or prefijo.startswith("cta:"):
            continue
        lineas.append(linea.rstrip())
    return "\n".join(lineas).strip()


def _referencia_valida(db: Database, reel_id: int):
    entry = db.get_bank_entry(reel_id)
    if entry is None:
        raise FlujoError(f"El reel {reel_id} no está en el banco. Solo valen outliers de 2× o más.")
    if entry.is_example:
        raise FlujoError(
            f"La ficha {reel_id} (@{entry.account}) es un ejemplo. No tiene métricas reales y no entra en el flujo."
        )
    if not entry.is_outlier:
        raise FlujoError(f"La ficha {reel_id} no llega al umbral de 2×.")
    return entry


def _exigir(db: Database, proyecto_id: int) -> Proyecto:
    proyecto = cargar_proyecto(db, proyecto_id)
    if proyecto is None:
        raise FlujoError(f"No existe el proyecto {proyecto_id}.")
    return proyecto
