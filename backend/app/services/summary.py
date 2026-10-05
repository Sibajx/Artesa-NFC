"""P-026 G5/G8: what needs attention, for Gestión's Resumen.

One read-only pass, shaped by role: everyone sees content, authorizations,
sales and the artisans' recent answers; designers also see designs;
custodians also see certification gaps. Lists are capped (the page links to
the full lists); counts are exact.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session, aliased

from app.core.access import CUSTODIAN, DESIGNER, AdminIdentity
from app.models.artisan import Artisan
from app.models.artisan_authorization import ArtisanAuthorization, AuthorizationStatus
from app.models.audit_event import AuditEvent
from app.models.certificate import Certificate, CertificateStatus
from app.models.certificate_design import CertificateDesign, DesignStatus
from app.models.enums import PublicationStatus
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.models.ownership import OwnershipCard, OwnershipCardStatus
from app.models.piece import Piece
from app.models.sale import Sale, SaleStatus

LIST_CAP = 8
RECENT_DAYS = 14


@dataclass
class Item:
    id: uuid.UUID
    name: str
    detail: str | None = None


@dataclass
class Bucket:
    count: int = 0
    items: list[Item] = field(default_factory=list)


def _bucket(db: Session, stmt, name_of, detail_of=None, id_of=None) -> Bucket:
    """``id_of``: the id the page links to (a piece's, for designs and cards)."""
    rows = db.execute(stmt).all()
    return Bucket(count=len(rows), items=[Item(id=id_of(r) if id_of else r[0].id, name=name_of(r),
                                               detail=detail_of(r) if detail_of else None)
                                          for r in rows[:LIST_CAP]])


def _live_pieces():
    return and_(Piece.trashed_at.is_(None))


def build(db: Session, identity: AdminIdentity) -> dict:
    now = datetime.now(timezone.utc)
    out: dict = {}

    # --- everyone -------------------------------------------------------------------
    authorized = exists().where(ArtisanAuthorization.artisan_id == Artisan.id,
                                ArtisanAuthorization.status == AuthorizationStatus.authorized)
    pending_auth = exists().where(ArtisanAuthorization.artisan_id == Artisan.id,
                                  ArtisanAuthorization.status == AuthorizationStatus.pending)
    out["published_artisans_without_authorization"] = _bucket(db, select(Artisan).where(
        Artisan.trashed_at.is_(None), Artisan.publication_status == PublicationStatus.published, ~authorized)
        .order_by(Artisan.full_name), lambda r: r[0].artistic_name or r[0].full_name)
    out["authorizations_waiting"] = _bucket(db, select(Artisan).where(Artisan.trashed_at.is_(None), pending_auth)
                                            .order_by(Artisan.full_name),
                                            lambda r: r[0].artistic_name or r[0].full_name)
    # "Quiero cambios" still unanswered: no open request and nothing decided
    # or revoked after it (authorizations.last_answer, in SQL).
    asked, newer = aliased(ArtisanAuthorization), aliased(ArtisanAuthorization)
    changes = exists().where(
        asked.artisan_id == Artisan.id, asked.status == AuthorizationStatus.changes_requested,
        ~exists().where(newer.artisan_id == asked.artisan_id,
                        newer.status.in_((AuthorizationStatus.pending, AuthorizationStatus.authorized))
                        | (func.coalesce(newer.revoked_at, newer.decided_at) > asked.decided_at)))
    out["authorizations_with_changes_requested"] = _bucket(
        db, select(Artisan).where(Artisan.trashed_at.is_(None), changes).order_by(Artisan.full_name),
        lambda r: r[0].artistic_name or r[0].full_name)
    since = now - timedelta(days=30)
    sales = db.execute(select(func.count(), func.coalesce(func.sum(Sale.price_cents), 0)).where(
        Sale.status == SaleStatus.active, Sale.created_at >= since)).one()
    out["sales_last_30_days"] = {"count": sales[0], "total_cents": int(sales[1])}

    # The artisans' answers through WhatsApp links (system actor = the link).
    recent = now - timedelta(days=RECENT_DAYS)
    answers = db.execute(select(AuditEvent).where(
        AuditEvent.occurred_at >= recent, AuditEvent.actor_email.is_(None),
        AuditEvent.action.in_(("artisan.authorized", "artisan.authorization_declined",
                               "artisan.authorization_changes_requested",
                               "design.approved", "design.changes_requested")))
        .order_by(AuditEvent.occurred_at.desc()).limit(LIST_CAP)).scalars().all()
    out["recent_answers"] = [_answer(db, e) for e in answers]

    # --- designers ----------------------------------------------------------------
    if identity.has(DESIGNER):
        def design_bucket(*conds):
            return _bucket(db, select(CertificateDesign, Piece.name).join(Piece, Piece.id == CertificateDesign.piece_id)
                           .where(*conds).order_by(CertificateDesign.updated_at.desc()),
                           lambda r: r[1], lambda r: f"versión {r[0].version}", lambda r: r[0].piece_id)
        out["designs_in_review"] = design_bucket(CertificateDesign.status == DesignStatus.in_review)
        out["designs_to_publish"] = design_bucket(CertificateDesign.status == DesignStatus.approved)
        out["designs_with_changes_requested"] = design_bucket(CertificateDesign.status == DesignStatus.draft,
                                                              CertificateDesign.change_request.is_not(None))

    # --- custodians ---------------------------------------------------------------
    if identity.has(CUSTODIAN):
        active_cert = exists().where(Certificate.piece_id == Piece.id, Certificate.status == CertificateStatus.active)
        written_tag = exists().where(NfcTag.piece_id == Piece.id,
                                     NfcTag.status.in_((NfcTagStatus.programmed, NfcTagStatus.locked)))
        card = exists().where(OwnershipCard.piece_id == Piece.id,
                              OwnershipCard.status.in_((OwnershipCardStatus.active, OwnershipCardStatus.blocked)))
        published = and_(_live_pieces(), Piece.publication_status == PublicationStatus.published)

        def piece_bucket(*conds):
            return _bucket(db, select(Piece).where(*conds).order_by(Piece.name), lambda r: r[0].name,
                           lambda r: r[0].public_code)
        out["published_without_certificate"] = piece_bucket(published, ~active_cert)
        out["certified_without_chip"] = piece_bucket(_live_pieces(), active_cert, ~written_tag)
        out["certified_without_card"] = piece_bucket(_live_pieces(), active_cert, ~card)
        out["reported_stolen"] = piece_bucket(_live_pieces(), Piece.reported_stolen_at.is_not(None))
        out["cards_blocked_or_locked"] = _bucket(db, select(OwnershipCard, Piece.name).join(
            Piece, Piece.id == OwnershipCard.piece_id).where(
            (OwnershipCard.status == OwnershipCardStatus.blocked) | (OwnershipCard.locked_until > now)),
            lambda r: r[1], lambda r: "bloqueada" if r[0].status == OwnershipCardStatus.blocked else "bloqueo temporal",
            lambda r: r[0].piece_id)
    return out


_ANSWER_TEXT = {
    "artisan.authorized": "autorizó su publicación",
    "artisan.authorization_declined": "no autorizó su publicación (se pasó a borrador)",
    "artisan.authorization_changes_requested": "pidió cambios antes de autorizar",
    "design.approved": "aprobó el diseño del certificado",
    "design.changes_requested": "pidió cambios al diseño del certificado",
}


def _answer(db: Session, e: AuditEvent) -> dict:
    if e.action.startswith("artisan."):
        artisan = db.get(Artisan, e.entity_id)
        who = (artisan.artistic_name or artisan.full_name) if artisan else "Un artesano"
        link = f"/artesanos/{e.entity_id}"
    else:
        piece = db.get(Piece, e.entity_id)
        who = f"El artesano de «{piece.name}»" if piece else "Un artesano"
        link = f"/diseno/{e.entity_id}"
    return {"at": e.occurred_at, "text": f"{who} {_ANSWER_TEXT[e.action]}", "link": link,
            "positive": e.action in ("artisan.authorized", "design.approved")}
