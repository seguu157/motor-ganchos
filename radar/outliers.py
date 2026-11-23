"""Mediana por cuenta y outliers.

Regla: de cada cuenta se toman como máximo los 20 reels más recientes que
tienen reproducciones (`videoPlayCount`). La mediana es la de ese grupo.
Un reel es outlier si su cifra es mayor o igual que 2 veces la mediana.
`videoViewCount` no entra en el cálculo.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

DEFAULT_SAMPLE_SIZE = 20
DEFAULT_THRESHOLD = 2.0


@dataclass(frozen=True)
class PlaySample:
    reel_key: str
    account: str
    play_count: int | None
    published_at: str | None = None


@dataclass(frozen=True)
class OutlierScore:
    reel_key: str
    account: str
    play_count: int
    median_plays: float
    multiple: float | None
    is_outlier: bool
    sample_size: int


def median_plays(values: list[int]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def detect_outliers(
    samples: list[PlaySample],
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[OutlierScore]:
    """Puntúa solo los reels dentro de la ventana reciente de cada cuenta."""
    if sample_size < 1:
        raise ValueError("La ventana tiene que ser de al menos 1 reel.")
    if threshold <= 0:
        raise ValueError("El umbral tiene que ser mayor que 0.")

    grouped: dict[str, list[PlaySample]] = {}
    for sample in samples:
        grouped.setdefault(sample.account, []).append(sample)

    scores: list[OutlierScore] = []
    for account, items in grouped.items():
        ordered = sorted(items, key=lambda item: (item.published_at or "", item.reel_key), reverse=True)
        window = ordered[:sample_size]
        plays = [item.play_count for item in window if item.play_count is not None]
        median = median_plays(plays)
        if median is None:
            continue
        for item in window:
            if item.play_count is None:
                continue
            multiple, is_outlier = _score(item.play_count, median, threshold)
            scores.append(
                OutlierScore(
                    reel_key=item.reel_key,
                    account=account,
                    play_count=item.play_count,
                    median_plays=median,
                    multiple=multiple,
                    is_outlier=is_outlier,
                    sample_size=len(plays),
                )
            )
    return scores


def _score(play_count: int, median: float, threshold: float) -> tuple[float | None, bool]:
    if median == 0:
        if play_count <= 0:
            return None, False
        return math.inf, True
    multiple = play_count / median
    # El umbral es inclusivo: 2,0x ya es outlier. El epsilon evita que un 2.0
    # binario quede por debajo por ruido de coma flotante.
    return multiple, multiple + 1e-9 >= threshold
