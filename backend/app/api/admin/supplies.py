"""Gestión → Insumos: stock of chips, seals, scratch cards, epoxy... and the
inventory of pieces. Reading is open to anyone who can see; recording and
editing needs the Logistics permission (it is Hariel's area)."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.admin.writes import _fail, actor, require_write_guard
from app.api.deps import get_db
from app.core import permissions as perms
from app.core.access import require_admin, require_permission
from app.models.artisan import Artisan
from app.models.piece import Piece
from app.models.sale import Sale, SaleStatus
from app.services import locations as locations_service
from app.services import supplies
from app.services.content import Actor, ContentError

reads = APIRouter(prefix="/api/admin/v1", tags=["admin", "supplies"], dependencies=[Depends(require_admin)])
writes = APIRouter(prefix="/api/admin/v1", tags=["admin", "supplies"],
                   dependencies=[Depends(require_permission(perms.LOGISTICS)), Depends(require_write_guard)])


class SupplyOut(BaseModel):
    id: uuid.UUID
    name: str
    unit: str
    min_stock: Decimal
    stock: Decimal
    low: bool
    note: str | None
    active: bool


class SupplyList(BaseModel):
    data: list[SupplyOut]
    low_count: int


class MovementOut(BaseModel):
    id: uuid.UUID
    kind: str
    delta: Decimal
    unit_cost_cents: int | None
    piece_id: uuid.UUID | None
    piece_name: str | None
    note: str | None
    recorded_by: str
    created_at: datetime


class SupplyDetail(BaseModel):
    supply: SupplyOut
    movements: list[MovementOut]


class SupplyBody(BaseModel):
    name: str = Field(max_length=120)
    unit: str = Field(max_length=30)
    min_stock: Decimal = Field(default=Decimal(0), ge=0)
    note: str | None = Field(default=None, max_length=500)


class SupplyPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    unit: str | None = Field(default=None, max_length=30)
    min_stock: Decimal | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)
    active: bool | None = None


class MovementBody(BaseModel):
    kind: Literal["purchase", "use", "loss", "adjustment"]
    quantity: Decimal = Field(gt=0)
    # Adjustments only: which way the count moves.
    direction: Literal["up", "down"] = "up"
    unit_cost_cents: int | None = Field(default=None, ge=0)
    piece_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=500)


def _out(r: supplies.SupplyRow) -> SupplyOut:
    s = r.supply
    return SupplyOut(id=s.id, name=s.name, unit=s.unit, min_stock=s.min_stock, stock=r.stock, low=r.low,
                     note=s.note, active=s.active)


def _detail(db: Session, supply_id: uuid.UUID) -> SupplyDetail:
    return SupplyDetail(
        supply=_out(supplies.row(db, supply_id)),
        movements=[MovementOut(id=m.id, kind=m.kind, delta=m.delta, unit_cost_cents=m.unit_cost_cents,
                               piece_id=m.piece_id, piece_name=name, note=m.note, recorded_by=m.recorded_by,
                               created_at=m.created_at) for m, name in supplies.movements(db, supply_id)])


@reads.get("/supplies", response_model=SupplyList)
def list_supplies(include_inactive: bool = False, db: Session = Depends(get_db)) -> SupplyList:
    rows = supplies.listing(db, include_inactive)
    return SupplyList(data=[_out(r) for r in rows], low_count=sum(1 for r in rows if r.low))


@reads.get("/supplies/{supply_id}", response_model=SupplyDetail)
def supply_detail(supply_id: uuid.UUID, db: Session = Depends(get_db)) -> SupplyDetail:
    try:
        return _detail(db, supply_id)
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/supplies", status_code=201, response_model=SupplyDetail)
def create_supply(body: SupplyBody, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> SupplyDetail:
    try:
        supply = supplies.create(db, who, body.name, body.unit, body.min_stock, body.note)
        return _detail(db, supply.id)
    except ContentError as exc:
        raise _fail(exc) from None


@writes.patch("/supplies/{supply_id}", response_model=SupplyDetail)
def update_supply(supply_id: uuid.UUID, body: SupplyPatch, who: Actor = Depends(actor),
                  db: Session = Depends(get_db)) -> SupplyDetail:
    try:
        supplies.update(db, who, supply_id, body.model_dump(exclude_unset=True))
        return _detail(db, supply_id)
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/supplies/{supply_id}/movements", status_code=201, response_model=SupplyDetail)
def record_movement(supply_id: uuid.UUID, body: MovementBody, who: Actor = Depends(actor),
                    db: Session = Depends(get_db)) -> SupplyDetail:
    try:
        supplies.record(db, who, supply_id, body.kind, body.quantity, unit_cost_cents=body.unit_cost_cents,
                        piece_id=body.piece_id, note=body.note, direction=body.direction)
        return _detail(db, supply_id)
    except ContentError as exc:
        raise _fail(exc) from None


# --- the inventory of pieces ------------------------------------------------------------


class InventoryRow(BaseModel):
    id: uuid.UUID
    public_code: str
    name: str
    artisan_id: uuid.UUID
    artisan_name: str
    publication_status: str
    availability_status: str
    price_cents: int | None
    price_currency: str
    location: str | None
    place: str | None
    sold_on: str | None
    updated_at: datetime


class InventorySummary(BaseModel):
    total: int
    by_availability: dict[str, int]
    available_value_cents: int
    currency: str


class Inventory(BaseModel):
    summary: InventorySummary
    data: list[InventoryRow]


@reads.get("/inventory", response_model=Inventory)
def inventory(db: Session = Depends(get_db)) -> Inventory:
    rows = db.execute(
        select(Piece, Artisan.full_name).join(Artisan, Artisan.id == Piece.artisan_id)
        .where(Piece.trashed_at.is_(None)).order_by(Artisan.full_name.asc(), Piece.public_code.asc())).all()
    ids = [p.id for p, _ in rows]
    sold = {}
    if ids:
        sold = {s.piece_id: s.sold_on for s in db.execute(
            select(Sale).where(Sale.piece_id.in_(ids), Sale.status == SaleStatus.active)).scalars()}
    where = locations_service.current_by_piece(db, ids)
    data = []
    counts: dict[str, int] = {}
    value = 0
    for piece, artisan_name in rows:
        status = piece.availability_status.value
        counts[status] = counts.get(status, 0) + 1
        if status == "available" and piece.price_cents is not None:
            value += piece.price_cents
        at = where.get(piece.id)
        data.append(InventoryRow(
            id=piece.id, public_code=piece.public_code, name=piece.name, artisan_id=piece.artisan_id,
            artisan_name=artisan_name, publication_status=piece.publication_status.value,
            availability_status=status, price_cents=piece.price_cents, price_currency=piece.price_currency,
            location=at.location if at else None, place=at.place if at else None,
            sold_on=sold[piece.id].isoformat() if piece.id in sold else None, updated_at=piece.updated_at))
    return Inventory(summary=InventorySummary(total=len(data), by_availability=counts,
                                              available_value_cents=value, currency="MXN"), data=data)
