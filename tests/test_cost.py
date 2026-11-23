from decimal import Decimal

from radar.cost import estimate_cost


def test_tandas_de_20_en_cinco_cuentas_cuestan_26_centavos():
    estimate = estimate_cost(5, 20)
    assert estimate.reels_total == 100
    assert estimate.estimated_usd == Decimal("0.26")
    assert estimate.usd_per_1000 == Decimal("2.60")
    assert estimate.monthly_credit_usd == Decimal("5.00")
    assert estimate.over_budget is False
    assert estimate.runs_fitting_in_credit == 19
    assert estimate.monthly_projection_usd == Decimal("1.04")
    assert estimate.reels_fitting_in_credit == 1923


def test_supera_el_credito_de_referencia():
    estimate = estimate_cost(5, 20_000)
    assert estimate.over_budget is True
