"""Stock of supplies (chips, seals, scratch cards, epoxy...), as a ledger.

The stock of a supply is the sum of its movements. A use, a loss or a negative
adjustment that would leave it below zero is refused, so the count never lies;
the supply row is locked while a movement is recorded, so two people cannot
spend the last unit at the same time. Every change is audited.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.piece import Piece
from app.models.supply import SUPPLY_MOVEMENT_KINDS, Supply, SupplyMovement
from app.services.content import Actor, ContentConflict, ContentNotFound, _audit

MAX_QUANTITY = Decimal("1000000")


@dataclass(frozen=True)
class SupplyRow:
    supply: Supply
    stock: Decimal

    @property
    def low(self) -> bool:
        """Active, with a minimum set, and at or under it."""
        return self.supply.active and self.supply.min_stock > 0 and self.stock <= self.supply.min_stock


def _number(value: object, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ContentConflict("invalid_quantity", "Escribe un número.", field) from None
    if not number.is_finite() or number < 0 or number > MAX_QUANTITY or (number == 0 and not allow_zero):
        raise ContentConflict("invalid_quantity", "La cantidad debe ser mayor que cero y razonable.", field)
    return number.quantize(Decimal("0.001"))


def _stocks(db: Session, ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, Decimal]:
    query = select(SupplyMovement.supply_id, func.coalesce(func.sum(SupplyMovement.delta), 0)).group_by(
        SupplyMovement.supply_id)
    if ids is not None:
        query = query.where(SupplyMovement.supply_id.in_(ids))
    return {sid: Decimal(total) for sid, total in db.execute(query).all()}


def listing(db: Session, include_inactive: bool = False) -> list[SupplyRow]:
    query = select(Supply).order_by(Supply.active.desc(), func.lower(Supply.name))
    if not include_inactive:
        query = query.where(Supply.active.is_(True))
    supplies = list(db.execute(query).scalars())
    stocks = _stocks(db, [s.id for s in supplies])
    return [SupplyRow(s, stocks.get(s.id, Decimal(0))) for s in supplies]


def get(db: Session, supply_id: uuid.UUID, *, lock: bool = False) -> Supply:
    query = select(Supply).where(Supply.id == supply_id)
    supply = db.execute(query.with_for_update() if lock else query).scalar_one_or_none()
    if supply is None:
        raise ContentNotFound("not_found", "Ese insumo no existe.")
    return supply


def row(db: Session, supply_id: uuid.UUID) -> SupplyRow:
    supply = get(db, supply_id)
    return SupplyRow(supply, _stocks(db, [supply.id]).get(supply.id, Decimal(0)))


def movements(db: Session, supply_id: uuid.UUID, limit: int = 100) -> list[tuple[SupplyMovement, str | None]]:
    get(db, supply_id)
    return list(db.execute(
        select(SupplyMovement, Piece.name).outerjoin(Piece, Piece.id == SupplyMovement.piece_id)
        .where(SupplyMovement.supply_id == supply_id)
        .order_by(SupplyMovement.created_at.desc()).limit(limit)).all())


def _clean_text(value: str | None, max_length: int) -> str | None:
    value = (value or "").strip()
    return value[:max_length] or None


def create(db: Session, actor: Actor, name: str, unit: str, min_stock: object, note: str | None) -> Supply:
    name, unit = name.strip(), unit.strip()
    if not name or not unit:
        raise ContentConflict("invalid_supply", "El nombre y la unidad son obligatorios.", "name")
    minimum = _number(min_stock, "min_stock", allow_zero=True)
    supply = Supply(name=name[:120], unit=unit[:30], min_stock=minimum, note=_clean_text(note, 500), active=True,
                    created_by=actor.identity.email)
    db.add(supply)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ContentConflict("duplicate", "Ya hay un insumo con ese nombre.", "name") from None
    _audit(db, actor, "supply", supply.id, "supply.created", {"name": supply.name, "unit": supply.unit})
    db.commit()
    return supply


def update(db: Session, actor: Actor, supply_id: uuid.UUID, changes: dict) -> Supply:
    supply = get(db, supply_id, lock=True)
    before = {}
    if "name" in changes:
        name = (changes["name"] or "").strip()
        if not name:
            raise ContentConflict("invalid_supply", "El nombre es obligatorio.", "name")
        before["name"], supply.name = supply.name, name[:120]
    if "unit" in changes:
        unit = (changes["unit"] or "").strip()
        if not unit:
            raise ContentConflict("invalid_supply", "La unidad es obligatoria.", "unit")
        before["unit"], supply.unit = supply.unit, unit[:30]
    if "min_stock" in changes:
        before["min_stock"], supply.min_stock = str(supply.min_stock), _number(changes["min_stock"], "min_stock", allow_zero=True)
    if "note" in changes:
        before["note"], supply.note = supply.note, _clean_text(changes["note"], 500)
    if "active" in changes:
        before["active"], supply.active = supply.active, bool(changes["active"])
    supply.updated_at = datetime.now(timezone.utc)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ContentConflict("duplicate", "Ya hay un insumo con ese nombre.", "name") from None
    _audit(db, actor, "supply", supply.id, "supply.updated", {"before": before})
    db.commit()
    return supply


def record(db: Session, actor: Actor, supply_id: uuid.UUID, kind: str, quantity: object, *,
           unit_cost_cents: int | None = None, piece_id: uuid.UUID | None = None, note: str | None = None,
           direction: str = "up") -> SupplyMovement:
    """``quantity`` is always positive; the kind decides the sign. For an
    adjustment ``direction`` ("up" or "down") says which way the count moves."""
    if kind not in SUPPLY_MOVEMENT_KINDS:
        raise ContentConflict("invalid_movement", "Tipo de movimiento desconocido.", "kind")
    amount = _number(quantity, "quantity")
    supply = get(db, supply_id, lock=True)
    if not supply.active:
        raise ContentConflict("inactive_supply", "Este insumo está desactivado. Actívalo para registrar movimientos.")
    if kind == "purchase":
        delta = amount
    elif kind in ("use", "loss"):
        delta = -amount
    else:
        delta = amount if direction == "up" else -amount
    if kind != "purchase" and unit_cost_cents is not None:
        raise ContentConflict("invalid_movement", "El costo solo se registra en compras.", "unit_cost_cents")
    if unit_cost_cents is not None and unit_cost_cents < 0:
        raise ContentConflict("invalid_movement", "El costo no puede ser negativo.", "unit_cost_cents")
    if piece_id is not None:
        if kind != "use":
            raise ContentConflict("invalid_movement", "Solo un uso se liga a una pieza.", "piece_id")
        if db.get(Piece, piece_id) is None:
            raise ContentNotFound("not_found", "Esa pieza no existe.")
    if kind in ("loss", "adjustment") and not (note or "").strip():
        raise ContentConflict("note_required", "Explica el motivo (merma o ajuste).", "note")
    stock = _stocks(db, [supply.id]).get(supply.id, Decimal(0))
    if stock + delta < 0:
        raise ContentConflict("insufficient_stock", f"No alcanza: hay {stock.normalize():f} {supply.unit}.", "quantity")
    movement = SupplyMovement(supply_id=supply.id, kind=kind, delta=delta, unit_cost_cents=unit_cost_cents,
                              piece_id=piece_id, note=_clean_text(note, 500), recorded_by=actor.identity.email,
                              # The real insert time (not the transaction start) so movements keep their order.
                              created_at=func.clock_timestamp())
    db.add(movement)
    db.flush()
    _audit(db, actor, "supply", supply.id, f"supply.{kind}", {
        "name": supply.name, "delta": str(delta), "stock_after": str(stock + delta),
        "unit_cost_cents": unit_cost_cents, "piece_id": str(piece_id) if piece_id else None})
    db.commit()
    return movement
