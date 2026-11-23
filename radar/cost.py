"""Estimación de coste del actor apify/instagram-reel-scraper.

La tarifa por defecto sale de la ficha pública del actor (plan Free, pago por
resultado): 2,60 USD por cada 1.000 reels, con 5 USD de crédito mensual.
No incluye el saldo real de la cuenta. Los add-ons de transcripción, descarga
de vídeo y recuento de compartidos quedan apagados y no entran en la cifra.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

# Valores de referencia. Se pueden sustituir con variables de entorno si Apify
# cambia la tarifa. Ver README.
DEFAULT_USD_PER_1000 = Decimal("2.60")
DEFAULT_MONTHLY_CREDIT_USD = Decimal("5.00")
WEEKS_PER_MONTH = 4


@dataclass(frozen=True)
class CostEstimate:
    accounts: int
    reels_per_account: int
    reels_total: int
    usd_per_1000: Decimal
    estimated_usd: Decimal
    monthly_credit_usd: Decimal

    @property
    def over_budget(self) -> bool:
        return self.estimated_usd > self.monthly_credit_usd

    @property
    def runs_fitting_in_credit(self) -> int:
        if self.estimated_usd <= 0:
            return 0
        return int(self.monthly_credit_usd // self.estimated_usd)

    @property
    def monthly_projection_usd(self) -> Decimal:
        """Cuatro tandas iguales, una por semana. Es una proyección, no un cargo."""
        return (self.estimated_usd * WEEKS_PER_MONTH).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def reels_fitting_in_credit(self) -> int:
        unit = self.usd_per_1000 / Decimal(1000)
        if unit <= 0:
            return 0
        return int(self.monthly_credit_usd / unit)


def estimate_cost(
    accounts: int,
    reels_per_account: int,
    *,
    usd_per_1000: Decimal = DEFAULT_USD_PER_1000,
    monthly_credit_usd: Decimal = DEFAULT_MONTHLY_CREDIT_USD,
) -> CostEstimate:
    if accounts < 0 or reels_per_account < 0:
        raise ValueError("Las cuentas y los reels no pueden ser negativos.")
    total = accounts * reels_per_account
    estimated = (Decimal(total) * usd_per_1000) / Decimal(1000)
    estimated = estimated.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return CostEstimate(
        accounts=accounts,
        reels_per_account=reels_per_account,
        reels_total=total,
        usd_per_1000=usd_per_1000,
        estimated_usd=estimated,
        monthly_credit_usd=monthly_credit_usd,
    )
