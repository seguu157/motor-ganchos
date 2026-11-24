"""Contrato común de una fuente de reels.

`fetch_reels` devuelve dictos con la forma del dataset de Apify. El parser
existente los convierte al modelo interno, así que la mediana, la
transcripción y el LLM no cambian.
"""

from __future__ import annotations

from typing import Any, Protocol


class ReelSource(Protocol):
    name: str

    def fetch_reels(self, usernames: list[str], results_limit: int) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...
