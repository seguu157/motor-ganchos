"""Adaptador de YouTube vía TubeAlfred. Vacío y desactivado.

Variable de entorno
-------------------
``TUBEALFRED_API_KEY``
    Reservada. La interfaz solo dice si existe. Este módulo no la envía a
    ningún sitio: ``enabled`` es siempre ``False``. Tener la clave no activa
    nada, a propósito.

Cómo activarlo más adelante
----------------------------
1. Confirmar en la documentación vigente de TubeAlfred el endpoint, la
   autenticación y los campos de reproducciones. No dejes aquí una URL
   inventada.
2. Implementar ``fetch_recent`` para devolver una lista de objetos con:
   canal, url, título, fecha ISO y reproducciones. Nada más hasta que el
   dato exista en la respuesta.
3. No mezclar la mediana de YouTube con la de Instagram. Hace falta otra
   plataforma (columna ``platform`` o tabla propia) y su propia ventana de
   últimos vídeos.
4. Cambiar ``enabled`` para que exija las dos cosas: la clave y un flag
   explícito ``TUBEALFRED_ENABLED=1``.
5. Añadir tests con una respuesta de ejemplo marcada como fixture, sin
   llamar a la API real.
"""

from __future__ import annotations

from dataclasses import dataclass


class TubeAlfredDisabled(RuntimeError):
    """TubeAlfred sigue desactivado en este MVP."""


@dataclass(frozen=True)
class TubeAlfredAdapter:
    api_key: str | None = None

    @property
    def enabled(self) -> bool:
        return False

    @property
    def key_configured(self) -> bool:
        return bool(self.api_key)

    def fetch_recent(self, channel: str, limit: int = 20) -> list[dict]:
        raise TubeAlfredDisabled(
            "TubeAlfred está desactivado. Definir TUBEALFRED_API_KEY no lo enciende. "
            "Cuando haya documentación de la API, implementa fetch_recent en radar/tubealfred.py."
        )
