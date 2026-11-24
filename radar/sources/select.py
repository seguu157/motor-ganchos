"""Elige la fuente antes de gastar crédito o de abrir Instagram.

La fuente por defecto es el navegador. Apify e Instaloader se piden aparte.
`auto` mantiene el criterio anterior cuando no hay sesión de navegador:
Apify si hay token y el coste cabe; si no, Instaloader.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from radar.config import Settings
from radar.cost import CostEstimate, estimate_cost

MODES = ("playwright", "apify", "instaloader", "auto")


class UnknownSource(ValueError):
    """La fuente pedida no es playwright, apify, instaloader ni auto."""


@dataclass(frozen=True)
class SourcePlan:
    requested: str
    effective: str
    reason: str
    apify_estimate: CostEstimate
    estimate: CostEstimate
    usernames: list[str]
    skipped: list[str]
    max_accounts: int


def normalize_mode(value: str | None, *, default: str = "auto") -> str:
    mode = (value or default).strip().lower()
    if mode not in MODES:
        raise UnknownSource("La fuente tiene que ser playwright, apify, instaloader o auto.")
    return mode


def plan_source(
    settings: Settings,
    usernames: list[str],
    reels_per_account: int,
    *,
    requested: str | None = None,
    allow_over_budget: bool = False,
) -> SourcePlan:
    mode = normalize_mode(requested, default=settings.radar_source)
    apify_estimate = estimate_cost(
        len(usernames),
        reels_per_account,
        usd_per_1000=settings.usd_per_1000,
        monthly_credit_usd=settings.monthly_credit_usd,
    )
    if mode == "playwright":
        effective = "playwright"
        reason = "Fuente por defecto: navegador con sesión persistente."
    elif mode == "apify":
        effective = "apify"
        reason = "Fuente pedida: Apify."
    elif mode == "instaloader":
        effective = "instaloader"
        reason = "Fuente pedida: Instaloader."
    elif settings.browser_ready:
        effective = "playwright"
        reason = "Hay sesión de navegador. En modo auto se usa antes que Apify."
    elif settings.apify_token and (not apify_estimate.over_budget or allow_over_budget):
        effective = "apify"
        reason = "Hay APIFY_TOKEN y la estimación cabe en el crédito de referencia."
    elif not settings.apify_token:
        effective = "instaloader"
        reason = "No hay APIFY_TOKEN ni sesión de navegador. Se usa el respaldo Instaloader."
    else:
        effective = "instaloader"
        reason = "La estimación de Apify supera el crédito de referencia. Se usa Instaloader."

    if effective == "playwright":
        cap = settings.browser_max_accounts
        chosen = list(usernames[:cap])
        skipped = list(usernames[cap:])
        posts = min(reels_per_account, settings.browser_max_posts)
        estimate = estimate_cost(
            len(chosen),
            posts,
            usd_per_1000=Decimal("0"),
            monthly_credit_usd=settings.monthly_credit_usd,
        )
    elif effective == "instaloader":
        cap = settings.ig_max_accounts
        chosen = list(usernames[:cap])
        skipped = list(usernames[cap:])
        estimate = estimate_cost(
            len(chosen),
            reels_per_account,
            usd_per_1000=Decimal("0"),
            monthly_credit_usd=settings.monthly_credit_usd,
        )
    else:
        cap = len(usernames)
        chosen = list(usernames)
        skipped = []
        estimate = apify_estimate
    return SourcePlan(
        requested=mode,
        effective=effective,
        reason=reason,
        apify_estimate=apify_estimate,
        estimate=estimate,
        usernames=chosen,
        skipped=skipped,
        max_accounts=cap,
    )


def block_reason(settings: Settings, plan: SourcePlan, *, allow_over_budget: bool) -> str | None:
    """Si no se puede lanzar, un texto para la interfaz. None si se puede."""
    if plan.effective == "playwright":
        if settings.browser_ready:
            return None
        return (
            "Falta la sesión del navegador. Ejecuta `radar login` una vez "
            "e inicia sesión a mano con la cuenta secundaria. "
            "No se ha hecho ninguna petición."
        )
    if plan.effective == "apify":
        if plan.apify_estimate.over_budget and not allow_over_budget:
            from radar.pipeline import OverBudget

            return str(OverBudget(plan.apify_estimate))
        if not settings.apify_token:
            return "Falta APIFY_TOKEN. No se ha hecho ninguna petición."
        return None
    if settings.ig_ready:
        return None
    if not settings.apify_token:
        return (
            "Falta APIFY_TOKEN. No se ha hecho ninguna petición. "
            "El respaldo Instaloader no está listo: define IG_USERNAME e IG_SESSION_FILE "
            "y ejecuta radar ig-login."
        )
    return (
        "La estimación de Apify no cabe en el crédito y no hay sesión de Instaloader. "
        "No se ha hecho ninguna petición."
    )
