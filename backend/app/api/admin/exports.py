"""P-026 G11: export the inventory and its certification state as CSV.

One row per piece outside the trash. Every admin gets the inventory columns;
custodians also get the certification columns (ADR-030: certificates, chips
and cards belong to the custody area). Buyer names and contacts never leave
the sale row. Each export is recorded in audit_event.

The file opens directly in Excel and LibreOffice: UTF-8 with BOM, and any
cell that starts like a formula is prefixed with an apostrophe so a piece
name such as ``=HYPERLINK(...)`` is shown as text, never evaluated.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.admin.custody import custody_pieces
from app.api.admin.writes import actor
from app.api.deps import get_db
from app.core import permissions as perms
from app.core.access import require_admin
from app.models.artisan import Artisan
from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.models.piece import Piece
from app.models.sale import Sale, SaleStatus
from app.services import locations as locations_service
from app.services.content import Actor

# Mexico (Oaxaca): UTC-6, no daylight saving time since 2022.
_LOCAL = timezone(timedelta(hours=-6))
_EXPORT_NAMESPACE = uuid.UUID("0b7c3f52-6d1e-4a8e-9f0a-c5e1d2b3a4f5")

router = APIRouter(prefix="/api/admin/v1/exports", tags=["admin"], dependencies=[Depends(require_admin)])

_PUBLICATION = {"draft": "Borrador", "published": "Publicado", "archived": "Archivado"}
_AVAILABILITY = {"available": "Disponible", "reserved": "Reservada", "exhibited": "En exhibición",
                 "archived": "Archivada", "sold": "Vendida"}
_CERTIFICATE = {"draft": "Borrador", "active": "Activo", "revoked": "Revocado"}
_TAG = {"available": "Disponible", "programmed": "Programado", "locked": "Bloqueado"}
_CARD = {"active": "Activa", "blocked": "Bloqueada"}
_LOCATION = {"taller": "Taller del artesano", "bodega": "Bodega", "tienda": "Tienda",
             "exhibicion": "Exhibición o feria", "transito": "En tránsito",
             "entregada": "Entregada al comprador", "otro": "Otro"}
_DESIGN = {"draft": "Borrador", "in_review": "Con el artesano", "approved": "Aprobado",
           "published": "Publicado", "superseded": "Anterior"}

_INVENTORY = ["Código", "Pieza", "Artesano", "Publicación", "Disponibilidad", "Precio", "Moneda",
              "Ubicación", "Lugar", "Técnica", "Materiales", "Año", "Fecha de venta", "Precio de venta", "Canal de venta",
              "Creada", "Actualizada"]
_CUSTODY = ["Certificado", "Versión del certificado", "Chip", "Modelo del chip", "Tarjeta",
            "Con dueño", "Diseño del certificado", "Reportada como robada"]

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Sí" if value else "No"
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _money(cents: int | None) -> str:
    return "" if cents is None else f"{cents // 100}.{cents % 100:02d}"


def _local(value: datetime | None) -> str:
    return value.astimezone(_LOCAL).strftime("%Y-%m-%d %H:%M") if value else ""


def _label(labels: dict[str, str], value: str | None) -> str | None:
    return labels.get(value, value) if value else None


@router.get("/pieces.csv")
def export_pieces(db: Session = Depends(get_db), who: Actor = Depends(actor)) -> Response:
    rows = db.execute(
        select(Piece, Artisan.full_name)
        .join(Artisan, Artisan.id == Piece.artisan_id)
        .where(Piece.trashed_at.is_(None))
        .order_by(Artisan.full_name.asc(), Piece.public_code.asc())
    ).all()
    ids = [p.id for p, _ in rows]
    sales = {}
    if ids:
        sales = {s.piece_id: s for s in db.execute(
            select(Sale).where(Sale.piece_id.in_(ids), Sale.status == SaleStatus.active)).scalars()}
    where = locations_service.current_by_piece(db, ids)
    with_custody = who.identity.can(perms.NFC)
    custody = {c.id: c for c in custody_pieces(db).data} if with_custody else {}

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(_INVENTORY + (_CUSTODY if with_custody else []))
    for piece, artisan_name in rows:
        sale = sales.get(piece.id)
        at = where.get(piece.id)
        line = [
            piece.public_code, piece.name, artisan_name,
            _label(_PUBLICATION, piece.publication_status.value),
            _label(_AVAILABILITY, piece.availability_status.value),
            _money(piece.price_cents), piece.price_currency if piece.price_cents is not None else None,
            _label(_LOCATION, at.location) if at else None, at.place if at else None,
            piece.technique, ", ".join(str(m) for m in piece.materials or []), piece.creation_year,
            sale.sold_on.isoformat() if sale else None,
            _money(sale.price_cents) if sale else None,
            sale.channel if sale else None,
            _local(piece.created_at), _local(piece.updated_at),
        ]
        if with_custody:
            c = custody.get(piece.id)
            line += [
                _label(_CERTIFICATE, c.certificate_status) if c else None,
                c.certificate_version if c else None,
                _label(_TAG, c.tag_status) if c else None,
                c.tag_chip if c else None,
                _label(_CARD, c.card_status) if c else None,
                c.claimed if c else None,
                _label(_DESIGN, c.design_status) if c else None,
                c.reported_stolen if c else None,
            ]
        writer.writerow([_cell(v) for v in line])

    db.add(AuditEvent(
        occurred_at=func.clock_timestamp(),
        actor_type=AuditActorType.admin_user,
        actor_email=who.identity.email,
        entity_type="export",
        entity_id=uuid.uuid5(_EXPORT_NAMESPACE, "pieces"),
        action="export.pieces",
        result=AuditResult.success,
        ip_address=who.ip_address,
        event_metadata={"rows": len(rows), "custody_columns": with_custody},
    ))
    db.commit()

    stamp = datetime.now(_LOCAL).strftime("%Y-%m-%d")
    return Response(
        content="﻿" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="artesanfc-piezas-{stamp}.csv"'},
    )
