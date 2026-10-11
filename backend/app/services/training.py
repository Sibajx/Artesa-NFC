"""Capacitaciones: training manuals per craft, written and with drawings, that the
owner reviews in Gestión. Status: draft -> in_review -> approved | changes_requested
(-> in_review again). Editing the content of a manual that is in review or approved
sends it back to draft, so an approval always refers to what was read."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.training import TrainingImage, TrainingManual
from app.services import mailer, private_photos
from app.services.content import Actor, ContentConflict, ContentNotFound, _audit

logger = logging.getLogger(__name__)

MAX_IMAGES = 12
MIN_SUBMIT_WRITTEN = 20
MIN_CHANGES_NOTE = 10
GESTION_URL = "https://gestion.artesanfc.com/"


def _clean(value: str | None, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get(db: Session, manual_id: uuid.UUID) -> TrainingManual:
    manual = db.get(TrainingManual, manual_id)
    if manual is None:
        raise ContentNotFound("not_found", "Ese manual no existe.")
    return manual


def listing(db: Session, craft: str | None = None, limit: int = 200) -> list[TrainingManual]:
    query = select(TrainingManual).order_by(TrainingManual.craft, TrainingManual.title).limit(limit)
    if craft:
        query = query.where(TrainingManual.craft == craft)
    return list(db.execute(query).scalars())


def images_of(db: Session, manual_id: uuid.UUID) -> list[TrainingImage]:
    return list(db.execute(select(TrainingImage).where(TrainingImage.manual_id == manual_id)
                           .order_by(TrainingImage.created_at, TrainingImage.id)).scalars())


def _content_changed(manual: TrainingManual) -> None:
    """Content moved: an approval (or a request for review) no longer covers it."""
    manual.updated_at = _now()
    if manual.status in ("in_review", "approved"):
        manual.status, manual.submitted_at = "draft", None


def _validated(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "title" in data:
        title = _clean(data["title"], 120)
        if title is None or len(title) < 3:
            raise ContentConflict("title_required", "El título necesita al menos 3 letras.", "title")
        out["title"] = title
    if "craft" in data:
        craft = _clean(data["craft"], 80)
        if craft is None or len(craft) < 2:
            raise ContentConflict("craft_required", "Escribe el oficio (por ejemplo, alfarería o textil).", "craft")
        out["craft"] = craft
    for key, limit in (("written", 20000), ("visual", 20000), ("next_steps", 3000)):
        if key in data:
            out[key] = _clean(data[key], limit)
    return out


def create(db: Session, actor: Actor, data: dict[str, Any]) -> TrainingManual:
    clean = _validated(data)
    if "title" not in clean or "craft" not in clean:
        _validated({"title": data.get("title"), "craft": data.get("craft")})
    manual = TrainingManual(created_by=actor.identity.email, status="draft", **clean)
    db.add(manual)
    db.flush()
    _audit(db, actor, "training_manual", manual.id, "training.created", {"craft": manual.craft})
    db.commit()
    return manual


def update(db: Session, actor: Actor, manual_id: uuid.UUID, changes: dict[str, Any]) -> TrainingManual:
    manual = get(db, manual_id)
    clean = _validated(changes)
    for key, value in clean.items():
        setattr(manual, key, value)
    was = manual.status
    _content_changed(manual)
    _audit(db, actor, "training_manual", manual.id, "training.updated",
           {"fields": sorted(clean), "back_to_draft": was != manual.status})
    db.commit()
    return manual


def submit(db: Session, actor: Actor, manual_id: uuid.UUID) -> TrainingManual:
    manual = get(db, manual_id)
    if manual.status not in ("draft", "changes_requested"):
        raise ContentConflict("invalid_status", "Este manual ya está en revisión o aprobado.")
    if len((manual.written or "").strip()) < MIN_SUBMIT_WRITTEN:
        raise ContentConflict("written_required", "Escribe la versión escrita antes de pedir la revisión.", "written")
    manual.status, manual.submitted_at, manual.updated_at = "in_review", _now(), _now()
    _audit(db, actor, "training_manual", manual.id, "training.submitted", {})
    db.commit()
    return manual


def review(db: Session, actor: Actor, manual_id: uuid.UUID, decision: str, note: str | None) -> TrainingManual:
    """The owner's answer. Approving may carry a note; asking for changes must say what."""
    manual = get(db, manual_id)
    if manual.status != "in_review":
        raise ContentConflict("invalid_status", "Solo se revisa un manual que está en revisión.")
    note = _clean(note, 3000)
    if decision == "changes" and len(note or "") < MIN_CHANGES_NOTE:
        raise ContentConflict("note_required", "Cuenta qué hay que cambiar (al menos 10 letras).", "note")
    if decision not in ("approve", "changes"):
        raise ContentConflict("invalid_decision", "Decisión desconocida.", "decision")
    manual.status = "approved" if decision == "approve" else "changes_requested"
    manual.review_note, manual.reviewed_by, manual.reviewed_at, manual.updated_at = note, actor.identity.email, _now(), _now()
    _audit(db, actor, "training_manual", manual.id, f"training.{'approved' if decision == 'approve' else 'changes_requested'}", {})
    db.commit()
    return manual


def delete(db: Session, actor: Actor, media_root: Path | None, manual_id: uuid.UUID) -> None:
    manual = get(db, manual_id)
    paths = [i.path for i in images_of(db, manual.id)]
    _audit(db, actor, "training_manual", manual.id, "training.deleted", {"craft": manual.craft, "images": len(paths)})
    db.delete(manual)
    db.commit()
    if media_root is not None:
        for path in paths:
            private_photos.remove(media_root, path)


def add_image(db: Session, actor: Actor, media_root: Path, manual_id: uuid.UUID, data: bytes,
              caption: str | None) -> TrainingImage:
    manual = get(db, manual_id)
    if len(images_of(db, manual.id)) >= MAX_IMAGES:
        raise ContentConflict("too_many_images", f"Cada manual admite hasta {MAX_IMAGES} dibujos.")
    path, width, height = private_photos.store(media_root, "capacitaciones", manual.id, data)
    image = TrainingImage(manual_id=manual.id, path=path, width=width, height=height,
                          caption=_clean(caption, 200), created_by=actor.identity.email)
    db.add(image)
    db.flush()
    _content_changed(manual)
    _audit(db, actor, "training_manual", manual.id, "training.image_added", {"image": str(image.id)})
    db.commit()
    return image


def remove_image(db: Session, actor: Actor, media_root: Path | None, image_id: uuid.UUID) -> TrainingManual:
    image = db.get(TrainingImage, image_id)
    if image is None:
        raise ContentNotFound("not_found", "Ese dibujo no existe.")
    manual, path = get(db, image.manual_id), image.path
    db.delete(image)
    _content_changed(manual)
    _audit(db, actor, "training_manual", manual.id, "training.image_removed", {"image": str(image_id)})
    db.commit()
    if media_root is not None:
        private_photos.remove(media_root, path)
    return manual


def image_file(db: Session, media_root: Path, image_id: uuid.UUID) -> Path:
    image = db.get(TrainingImage, image_id)
    path = private_photos.resolve(media_root, image.path) if image else None
    if path is None:
        raise ContentNotFound("not_found", "Ese dibujo no existe.")
    return path


# --- emails (best effort: without mail settings, or if the relay fails, nothing is sent) ----


def _send(recipients: list[str], subject: str, body: str) -> None:
    if not get_settings().mail_configured:
        return
    for to in recipients:
        try:
            mailer.send_email(to, subject, body)
        except (mailer.MailUnavailable, mailer.MailError, ValueError) as exc:
            logger.warning("training notice not sent: %s", type(exc).__name__)


def notify_submitted(title: str, craft: str, who: str) -> None:
    _send(get_settings().owner_emails_list, f"ArtesaNFC: «{title}» espera tu revisión",
          f"{who} pidió tu revisión del manual «{title}» ({craft}).\nRevísalo en Gestión → Capacitaciones: {GESTION_URL}\n")


def notify_reviewed(title: str, decision: str, note: str | None, author: str) -> None:
    approved = decision == "approve"
    said = f"\nLo que escribió:\n«{note}»\n" if note else ""
    _send([author], f"ArtesaNFC: «{title}» " + ("fue aprobado" if approved else "necesita cambios"),
          f"Tu manual «{title}» " + ("fue aprobado." if approved else "volvió con cambios.") + said +
          f"\nVer en Gestión → Capacitaciones: {GESTION_URL}\n")
