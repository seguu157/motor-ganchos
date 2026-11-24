"""Perfil de marca de Antonio. Lo que no está escrito queda en [X]."""

from __future__ import annotations

from dataclasses import dataclass

CAMPOS = (
    "marca",
    "oferta",
    "audiencia",
    "problema",
    "tono",
    "idioma",
    "pruebas",
    "restricciones",
    "cta",
    "nicho",
)

ETIQUETAS = {
    "marca": "Marca",
    "oferta": "Oferta",
    "audiencia": "Audiencia",
    "problema": "Problema",
    "tono": "Tono",
    "idioma": "Idioma",
    "pruebas": "Pruebas que sí se pueden citar",
    "restricciones": "Restricciones",
    "cta": "CTA",
    "nicho": "Nicho",
}


@dataclass
class Perfil:
    marca: str = ""
    oferta: str = ""
    audiencia: str = ""
    problema: str = ""
    tono: str = ""
    idioma: str = "es"
    pruebas: str = ""
    restricciones: str = ""
    cta: str = ""
    nicho: str = ""

    def hueco(self, campo: str) -> str:
        valor = str(getattr(self, campo, "") or "").strip()
        return valor or "[X]"

    def as_dict(self) -> dict[str, str]:
        return {campo: str(getattr(self, campo) or "") for campo in CAMPOS}


def perfil_desde_dict(data: dict | None) -> Perfil:
    datos = data or {}
    kwargs = {}
    for campo in CAMPOS:
        if datos.get(campo) is not None:
            kwargs[campo] = str(datos.get(campo))
    if "idioma" not in kwargs or not kwargs["idioma"].strip():
        kwargs["idioma"] = "es"
    return Perfil(**kwargs)


def perfil_desde_formulario(form) -> Perfil:
    datos = {campo: str(form.get(campo) or "") for campo in CAMPOS}
    return perfil_desde_dict(datos)
