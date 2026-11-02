"""Calculadora de precio al público (Gestión).

The artisan gets their price in full; Artesa adds its commission, the card fee
and the taxes, and rounds up (docs/superpowers/specs/2026-11-01-calculadora-precios-design.md).

Two tax models, chosen in the settings until the accountant decides:
- ``intermediario``: Artesa sells in the artisan's name and only bills its
  commission, so IVA goes on the commission alone; the IVA of the card fee is a
  cost.
- ``revendedor``: Artesa buys and resells, so IVA goes on the whole price; the
  IVA of the card fee is credited back.

ISR is an estimate on Artesa's gross commission, before deductions. Every
amount is integer cents; Decimal in between, never floats. The rates are
editable defaults, not tax advice.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.models.pricing import PricingSettings
from app.services.content import Actor, _audit

MAX_CENTS = 10_000_000_000
SETTINGS_ID = 1
# audit_event needs a UUID; the settings are a single row, so a fixed one.
SETTINGS_UUID = uuid.UUID("5e771a6e-0000-4000-8000-000000000001")


class Tier(BaseModel):
    up_to_cents: int | None = Field(default=None, ge=1, le=MAX_CENTS)  # None: no upper limit (the last tier)
    pct: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)


def _default_tiers() -> list[Tier]:
    return [Tier(up_to_cents=300000, pct=Decimal(25)), Tier(up_to_cents=1000000, pct=Decimal(20)),
            Tier(up_to_cents=None, pct=Decimal(15))]


class PricingConfig(BaseModel):
    mode: Literal["intermediario", "revendedor"] = "intermediario"
    tiers: list[Tier] = Field(default_factory=_default_tiers, max_length=10)
    card_pct: Decimal = Field(default=Decimal("3.6"), ge=0, lt=20, max_digits=5, decimal_places=2)
    card_fixed_cents: int = Field(default=0, ge=0, le=100000)
    iva_pct: Decimal = Field(default=Decimal(16), ge=0, le=100, max_digits=5, decimal_places=2)
    isr_pct: Decimal = Field(default=Decimal(30), ge=0, le=100, max_digits=5, decimal_places=2)
    ret_isr_pct: Decimal = Field(default=Decimal(0), ge=0, le=50, max_digits=5, decimal_places=2)
    ret_iva_pct: Decimal = Field(default=Decimal(0), ge=0, le=50, max_digits=5, decimal_places=2)
    direct_fee_cents: int = Field(default=50000, ge=0, le=MAX_CENTS)
    round_to_cents: int = Field(default=10000, ge=1, le=10_000_000)

    @model_validator(mode="after")
    def _tiers_make_sense(self) -> PricingConfig:
        if not self.tiers:
            raise ValueError("Hace falta al menos un tramo de comisión.")
        if self.tiers[-1].up_to_cents is not None:
            raise ValueError("El último tramo no lleva límite.")
        limits = [t.up_to_cents for t in self.tiers[:-1]]
        if any(x is None for x in limits) or limits != sorted(set(limits)):  # type: ignore[type-var]
            raise ValueError("Los límites de los tramos van de menor a mayor, sin repetir.")
        return self


class Quote(BaseModel):
    mode: str
    commission_pct: Decimal
    artisan_cents: int
    retentions_cents: int
    agreed_commission_cents: int
    exact_price_cents: int
    public_price_cents: int
    rounding_cents: int
    card_fee_cents: int
    iva_cents: int
    gross_commission_cents: int
    isr_cents: int
    net_cents: int
    direct_sale_cents: int


def _pct(value: Decimal) -> Decimal:
    return Decimal(value) / 100


def _cents(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def tier_pct(config: PricingConfig, artisan_cents: int) -> Decimal:
    for tier in config.tiers:
        if tier.up_to_cents is None or artisan_cents <= tier.up_to_cents:
            return tier.pct
    return config.tiers[-1].pct  # unreachable: the last tier is open


def quote(config: PricingConfig, artisan_cents: int, commission_pct: Decimal | float | int | None) -> Quote:
    a = Decimal(artisan_cents)
    pct = tier_pct(config, artisan_cents) if commission_pct is None else Decimal(str(commission_pct))
    iva, card, fixed = _pct(config.iva_pct), _pct(config.card_pct), Decimal(config.card_fixed_cents)
    agreed = _cents(a * _pct(pct))
    retentions = _cents(a * (_pct(config.ret_isr_pct) + _pct(config.ret_iva_pct)))
    base = a + retentions

    if artisan_cents == 0:
        exact = 0
    elif config.mode == "intermediario":
        exact = _cents((base + agreed * (1 + iva) + fixed * (1 + iva)) / (1 - card * (1 + iva)))
    else:
        exact = _cents((base + agreed + fixed) / (1 / (1 + iva) - card))
    step = config.round_to_cents
    public = int((Decimal(exact) / step).to_integral_value(rounding=ROUND_CEILING)) * step

    # Split the public price; what is left after the artisan, the retentions,
    # the card fee and IVA is Artesa's gross commission (rounding included).
    p = Decimal(public)
    if public == 0:
        fee = iva_cents = gross = 0
    elif config.mode == "intermediario":
        fee = _cents((p * card + fixed) * (1 + iva))
        gross = _cents((p - base - fee) / (1 + iva))
        iva_cents = public - artisan_cents - retentions - fee - gross
    else:
        fee = _cents(p * card + fixed)
        iva_cents = _cents(p - p / (1 + iva))
        gross = public - iva_cents - artisan_cents - retentions - fee
    isr = _cents(Decimal(max(gross, 0)) * _pct(config.isr_pct))
    return Quote(
        mode=config.mode, commission_pct=pct, artisan_cents=artisan_cents, retentions_cents=retentions,
        agreed_commission_cents=agreed, exact_price_cents=exact, public_price_cents=public,
        rounding_cents=public - exact, card_fee_cents=fee, iva_cents=iva_cents, gross_commission_cents=gross,
        isr_cents=isr, net_cents=gross - isr, direct_sale_cents=artisan_cents + config.direct_fee_cents,
    )


# ---------- settings (one row) ----------

def load(db: Session) -> tuple[PricingConfig, datetime | None, str | None]:
    row = db.get(PricingSettings, SETTINGS_ID)
    if row is None:
        return PricingConfig(), None, None
    return PricingConfig.model_validate(row.config), row.updated_at, row.updated_by


def save(db: Session, actor: Actor, config: PricingConfig) -> PricingConfig:
    row = db.get(PricingSettings, SETTINGS_ID, with_for_update=True)
    data = config.model_dump(mode="json")
    before = row.config if row else None
    if row is None:
        row = PricingSettings(id=SETTINGS_ID, config=data, updated_by=actor.identity.email)
        db.add(row)
    else:
        row.config, row.updated_by = data, actor.identity.email
    db.flush()
    _audit(db, actor, "pricing_settings", SETTINGS_UUID, "pricing.settings_updated", {"before": before, "after": data})
    db.commit()
    return config
