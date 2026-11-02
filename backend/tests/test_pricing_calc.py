"""Calculadora de precio al público: el cálculo puro (sin base de datos)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.pricing import PricingConfig, quote


def cfg(**changes) -> PricingConfig:
    return PricingConfig(**{**PricingConfig().model_dump(), **changes})


def test_example_of_the_po_intermediary():
    # $6,000 for the artisan, 20 % commission, 3.6 % card fee, IVA 16 %, ISR 30 %.
    q = quote(cfg(), 600000, 20)
    assert q.commission_pct == 20
    assert q.agreed_commission_cents == 120000
    assert q.exact_price_cents == 771414          # (6000 + 1200·1.16) / (1 − 0.036·1.16)
    assert q.public_price_cents == 780000         # next hundred up
    assert q.artisan_cents == 600000
    # Every peso of the public price lands somewhere.
    assert q.artisan_cents + q.retentions_cents + q.gross_commission_cents + q.iva_cents + q.card_fee_cents \
        == q.public_price_cents
    assert q.gross_commission_cents > q.agreed_commission_cents   # the rounding is Artesa's
    assert q.isr_cents == round(q.gross_commission_cents * 0.30)
    assert q.net_cents == q.gross_commission_cents - q.isr_cents
    assert 85000 < q.net_cents < 95000


def test_reseller_charges_iva_on_everything_and_costs_more():
    inter = quote(cfg(), 600000, 20)
    resale = quote(cfg(mode="revendedor"), 600000, 20)
    assert resale.public_price_cents > inter.public_price_cents
    assert resale.public_price_cents == 880000
    assert resale.artisan_cents + resale.retentions_cents + resale.gross_commission_cents + resale.iva_cents \
        + resale.card_fee_cents == resale.public_price_cents


def test_commission_comes_from_the_tiers_when_the_piece_has_none():
    assert quote(cfg(), 250000, None).commission_pct == 25     # up to $3,000
    assert quote(cfg(), 300000, None).commission_pct == 25     # the limit is inclusive
    assert quote(cfg(), 600000, None).commission_pct == 20     # up to $10,000
    assert quote(cfg(), 2500000, None).commission_pct == 15    # above


def test_rounding_is_always_up_and_configurable():
    q = quote(cfg(round_to_cents=5000), 600000, 20)
    assert q.public_price_cents == 775000
    exact = quote(cfg(round_to_cents=1), 600000, 20)
    assert exact.public_price_cents == exact.exact_price_cents


def test_platform_retentions_go_on_top_so_the_artisan_still_gets_it_all():
    q = quote(cfg(ret_isr_pct=1, ret_iva_pct=8), 600000, 20)
    assert q.retentions_cents == 54000
    assert q.artisan_cents == 600000
    assert q.public_price_cents > quote(cfg(), 600000, 20).public_price_cents


def test_direct_sale_reference():
    q = quote(cfg(), 600000, 20)
    assert q.direct_sale_cents == 650000


def test_zero_price_is_zero():
    q = quote(cfg(), 0, 20)
    assert q.public_price_cents == 0 and q.net_cents == 0


@pytest.mark.parametrize("changes", [
    {"iva_pct": -1}, {"isr_pct": 101}, {"card_pct": 50}, {"round_to_cents": 0},
    {"tiers": []},
    {"tiers": [{"up_to_cents": 300000, "pct": 25}]},                         # last tier must be open
    {"tiers": [{"up_to_cents": 500000, "pct": 25}, {"up_to_cents": 300000, "pct": 20},
               {"up_to_cents": None, "pct": 15}]},                          # not ascending
    {"mode": "otro"},
])
def test_invalid_settings_are_refused(changes):
    with pytest.raises(ValidationError):
        cfg(**changes)
