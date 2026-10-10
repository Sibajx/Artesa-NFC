"""P-028: the home hero by season, managed from Gestión → Hero.

Which campaign shows today (America/Mexico_City, UTC-6 with no DST since
2022, like the rest of the API):

1. the *forced* campaign, if any and not past its end date;
2. else a *published* campaign whose yearly range covers today (when several
   do, the one that started last);
3. else the *published* default ("Hero normal");
4. else none: the site keeps its built-in hero.

Only a campaign whose video is ``ready`` can show. A video goes through
``ffmpeg`` (never a shell, never a network protocol): cropped to 16:9, at most
12 s, no audio, 1080p at most, MP4 + WebM + a poster frame. That runs in the
background (``run_job``) so Gestión never waits. Public files are named by
content hash and are never rewritten; a replaced video's files are removed
only after the new ones are stored.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.models.hero_campaign import HeroCampaign
from app.services.content import Actor, ContentConflict, ContentError, ContentNotFound, _unique_slug, slugify

MEXICO = timezone(timedelta(hours=-6))
MAX_ORIGINAL_BYTES = 200 * 1024 * 1024
MAX_SECONDS = 12
MAX_OUTPUT_BYTES = 12 * 1024 * 1024
STALE_AFTER = timedelta(minutes=20)
_FFMPEG_TIMEOUT = 600
_FILTER = ("crop=w='trunc(min(iw,ih*16/9)/2)*2':h='trunc(min(ih,iw*9/16)/2)*2',"
           "scale='min(1920,iw)':-2,fps=30,format=yuv420p")
_NAME_MAX = 60


class HeroInvalid(ContentError):
    status_code = 422


class HeroTooLarge(ContentError):
    status_code = 413


class HeroUnavailable(ContentError):
    status_code = 503


def today_mx(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(MEXICO).date()


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- which campaign shows ---------------------------------------------------------------


def covers(c: HeroCampaign, today: date) -> bool:
    if c.start_month is None:
        return False
    here, start, end = (today.month, today.day), (c.start_month, c.start_day), (c.end_month, c.end_day)
    return start <= here <= end if start <= end else here >= start or here <= end


def _days_since_start(c: HeroCampaign, today: date) -> int:
    start = date(today.year, c.start_month, min(c.start_day, 28 if c.start_month == 2 else c.start_day))
    if start > today:
        start = start.replace(year=today.year - 1)
    return (today - start).days


def _forced_now(c: HeroCampaign, today: date) -> bool:
    return c.forced and (c.forced_until is None or today <= c.forced_until)


def _is_ready(c: HeroCampaign) -> bool:
    # Files, not the job state: while a replacement is processing (or failed)
    # the previous video keeps showing.
    return bool(c.video_mp4 and c.poster)


def active(campaigns: list[HeroCampaign], today: date) -> tuple[HeroCampaign | None, str | None]:
    """The campaign that shows on ``today`` and why: forced | date | default."""
    ready = [c for c in campaigns if _is_ready(c)]
    for c in ready:
        if _forced_now(c, today):
            return c, "forced"
    dated = [c for c in ready if c.published and covers(c, today)]
    if dated:
        return min(dated, key=lambda c: _days_since_start(c, today)), "date"
    for c in ready:
        if c.is_default and c.published:
            return c, "default"
    return None, None


def all_campaigns(db: Session) -> list[HeroCampaign]:
    return list(db.execute(select(HeroCampaign).order_by(HeroCampaign.is_default.desc(), HeroCampaign.start_month,
                                                       HeroCampaign.start_day, HeroCampaign.name)).scalars())


def current(db: Session, today: date | None = None) -> tuple[HeroCampaign | None, str | None]:
    return active(all_campaigns(db), today or today_mx())


def status_of(c: HeroCampaign, live: HeroCampaign | None, now: datetime | None = None) -> str:
    """no_video | processing | error | draft | live | scheduled."""
    state = c.processing_status
    if state == "processing":
        started = c.processing_started_at
        if started is not None and (now or datetime.now(timezone.utc)) - started > STALE_AFTER:
            return "error"
        return "processing"
    if state == "error":
        return "error"
    if not _is_ready(c):
        return "no_video"
    if live is not None and live.id == c.id:
        return "live"
    return "scheduled" if c.published else "draft"


def error_of(c: HeroCampaign, now: datetime | None = None) -> str | None:
    if c.processing_status == "processing" and status_of(c, None, now) == "error":
        return "El procesamiento se interrumpió (¿se reinició el servidor?). Sube el video otra vez."
    return c.processing_error if c.processing_status == "error" else None


# --- audit and small helpers ------------------------------------------------------------


def _audit(db: Session, actor: Actor, c: HeroCampaign, action: str, metadata: dict[str, Any] | None = None) -> None:
    db.add(AuditEvent(occurred_at=func.clock_timestamp(), actor_type=AuditActorType.admin_user,
                      actor_email=actor.identity.email, entity_type="hero_campaign", entity_id=c.id,
                      action=f"hero.{action}", result=AuditResult.success, ip_address=actor.ip_address,
                      event_metadata={"slug": c.slug, **(metadata or {})}))


def _get(db: Session, campaign_id: uuid.UUID, *, lock: bool = False) -> HeroCampaign:
    stmt = select(HeroCampaign).where(HeroCampaign.id == campaign_id)
    c = db.execute(stmt.with_for_update() if lock else stmt).scalar_one_or_none()
    if c is None:
        raise ContentNotFound("not_found", "Esa temporada no existe.")
    return c


def _check_day(month: int, day: int, field: str) -> None:
    try:
        date(2024, month, day)  # a leap year: Feb 29 is a valid yearly date
    except ValueError:
        raise HeroInvalid("invalid_date", "Esa fecha no existe.", field) from None


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name or len(name) > _NAME_MAX:
        raise HeroInvalid("invalid_name", f"El nombre debe tener de 1 a {_NAME_MAX} letras.", "name")
    return name


def _clean_range(values: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    _check_day(values[0], values[1], "start")
    _check_day(values[2], values[3], "end")
    return values


# --- campaigns ----------------------------------------------------------------------------


def create(db: Session, actor: Actor, name: str, start: tuple[int, int], end: tuple[int, int]) -> HeroCampaign:
    name = _clean_name(name)
    sm, sd, em, ed = _clean_range((*start, *end))
    c = HeroCampaign(slug=_unique_slug(db, HeroCampaign, slugify(name)), name=name, is_default=False,
                     start_month=sm, start_day=sd, end_month=em, end_day=ed, updated_by=actor.identity.email)
    c.published = c.forced = False
    c.processing_status = "none"
    db.add(c)
    db.flush()
    _audit(db, actor, c, "created", {"name": name})
    db.commit()
    return c


def update(db: Session, actor: Actor, campaign_id: uuid.UUID, name: str | None,
           start: tuple[int, int] | None, end: tuple[int, int] | None) -> HeroCampaign:
    c = _get(db, campaign_id, lock=True)
    changes: dict[str, Any] = {}
    if name is not None:
        c.name = changes["name"] = _clean_name(name)
    if (start is None) != (end is None):
        raise HeroInvalid("invalid_date", "Indica el inicio y el fin.", "start")
    if start is not None and end is not None:
        if c.is_default:
            raise HeroInvalid("default_has_no_dates", "El hero normal no tiene fechas.", "start")
        c.start_month, c.start_day, c.end_month, c.end_day = _clean_range((*start, *end))
        changes["range"] = [c.start_month, c.start_day, c.end_month, c.end_day]
    c.updated_by = actor.identity.email
    _audit(db, actor, c, "updated", changes)
    db.commit()
    return c


def publish(db: Session, actor: Actor, campaign_id: uuid.UUID, published: bool) -> HeroCampaign:
    c = _get(db, campaign_id, lock=True)
    if published and not _is_ready(c):
        raise ContentConflict("no_video", "Sube un video y espera a que esté listo antes de publicar.")
    c.published, c.updated_by = published, actor.identity.email
    _audit(db, actor, c, "published" if published else "unpublished")
    db.commit()
    return c


def force(db: Session, actor: Actor, campaign_id: uuid.UUID, until: date | None) -> HeroCampaign:
    """Shows this campaign for everyone now, whatever the date, optionally until a day."""
    c = _get(db, campaign_id, lock=True)
    if not _is_ready(c):
        raise ContentConflict("no_video", "Esa temporada todavía no tiene un video listo.")
    if until is not None and until < today_mx():
        raise HeroInvalid("invalid_date", "La fecha de fin ya pasó.", "until")
    previous = db.execute(select(HeroCampaign).where(HeroCampaign.forced.is_(True), HeroCampaign.id != c.id)
                          .with_for_update()).scalar_one_or_none()
    if previous is not None:
        previous.forced, previous.forced_until = False, None
        db.flush()  # the partial unique index allows one forced campaign
    c.forced, c.forced_until, c.updated_by = True, until, actor.identity.email
    _audit(db, actor, c, "forced", {"until": until.isoformat() if until else None})
    db.commit()
    return c


def unforce(db: Session, actor: Actor) -> None:
    c = db.execute(select(HeroCampaign).where(HeroCampaign.forced.is_(True)).with_for_update()).scalar_one_or_none()
    if c is None:
        return
    c.forced, c.forced_until, c.updated_by = False, None, actor.identity.email
    _audit(db, actor, c, "unforced")
    db.commit()


def delete(db: Session, actor: Actor, public_root: Path | None, campaign_id: uuid.UUID) -> None:
    c = _get(db, campaign_id, lock=True)
    if c.is_default:
        raise ContentConflict("default_campaign", "El hero normal no se puede borrar.")
    if c.forced:
        raise ContentConflict("forced_campaign", "Quita primero el forzado de esta temporada.")
    paths = [p for p in (c.video_mp4, c.video_webm, c.poster) if p]
    slug = c.slug
    _audit(db, actor, c, "deleted", {"name": c.name})
    db.delete(c)
    db.commit()
    if public_root is not None:
        _remove_files(public_root, paths)
        shutil.rmtree(public_root / "hero" / slug, ignore_errors=True)


# --- the video ----------------------------------------------------------------------------


def _looks_like_video(data: bytes) -> bool:
    # MP4/MOV ("ftyp" box) or Matroska/WebM (EBML header).
    return data[4:8] == b"ftyp" or data[:4] == b"\x1a\x45\xdf\xa3"


def begin_upload(db: Session, actor: Actor, media_root: Path, campaign_id: uuid.UUID, data: bytes) -> Path:
    """Validates and stores the original, and marks the campaign as processing.
    Returns the original's path for ``run_job``."""
    if not ffmpeg_available():
        raise HeroUnavailable("ffmpeg_unavailable", "El servidor no tiene ffmpeg instalado.")
    if len(data) > MAX_ORIGINAL_BYTES:
        raise HeroTooLarge("too_large", "El video pesa más de 200 MB.")
    if not _looks_like_video(data):
        raise HeroInvalid("unsupported_media_type", "El archivo no parece un video MP4, MOV o WebM.")
    c = _get(db, campaign_id, lock=True)
    now = datetime.now(timezone.utc)
    if c.processing_status == "processing" and c.processing_started_at and now - c.processing_started_at < STALE_AFTER:
        raise ContentConflict("already_processing", "Esta temporada ya está procesando un video.")
    folder = media_root / "originales" / "hero"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    original = folder / f"{c.slug}-{uuid.uuid4().hex[:8]}.src"
    fd = os.open(original, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    c.processing_status, c.processing_error, c.processing_started_at = "processing", None, now
    c.updated_by = actor.identity.email
    _audit(db, actor, c, "video_uploaded", {"bytes": len(data)})
    db.commit()
    return original


@dataclass(frozen=True)
class Rendered:
    mp4: Path
    webm: Path
    poster: Path


def _run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, capture_output=True, timeout=_FFMPEG_TIMEOUT, check=False, stdin=subprocess.DEVNULL)


def _probe(original: Path) -> float:
    done = _run(["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_type:format=duration", "-of", "default=nw=1", str(original)])
    text = done.stdout.decode("utf-8", "replace")
    if done.returncode != 0 or "codec_type=video" not in text:
        raise HeroInvalid("not_a_video", "No se pudo leer un video en ese archivo.")
    try:
        return float(text.split("duration=")[1].split()[0])
    except (IndexError, ValueError):
        return 0.0


def _ffmpeg(original: Path, out: Path, *options: str, seek: float = 0.0) -> None:
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file"]
    if seek:
        args += ["-ss", str(seek)]
    args += ["-i", str(original), *options, str(out)]
    done = _run(args)
    if done.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise HeroInvalid("encode_failed", "No se pudo convertir ese video. Prueba con otro archivo.")


def render(original: Path, workdir: Path) -> Rendered:
    """ffmpeg: 16:9, <= 12 s, no audio, <= 1080p, MP4 + WebM + poster. Raises HeroInvalid."""
    _probe(original)
    base = ("-t", str(MAX_SECONDS), "-an", "-vf", _FILTER)
    mp4 = workdir / "out.mp4"
    for crf in ("28", "34"):
        mp4.unlink(missing_ok=True)
        _ffmpeg(original, mp4, *base, "-c:v", "libx264", "-crf", crf, "-preset", "slow", "-movflags", "+faststart")
        if mp4.stat().st_size <= MAX_OUTPUT_BYTES:
            break
    else:
        raise HeroInvalid("too_heavy", "El video queda demasiado pesado aun comprimido. Usa uno más corto o sencillo.")
    webm = workdir / "out.webm"
    _ffmpeg(original, webm, *base, "-c:v", "libvpx-vp9", "-crf", "36", "-b:v", "0", "-row-mt", "1")
    poster = workdir / "out.jpg"
    try:
        _ffmpeg(original, poster, "-frames:v", "1", "-vf", _FILTER, "-q:v", "3", seek=1.0)
    except HeroInvalid:  # shorter than a second: take the first frame
        _ffmpeg(original, poster, "-frames:v", "1", "-vf", _FILTER, "-q:v", "3")
    return Rendered(mp4, webm, poster)


def _store(public_root: Path, slug: str, rendered: Rendered) -> tuple[str, str, str]:
    stem = hashlib.sha256(rendered.mp4.read_bytes()).hexdigest()[:12]
    folder = public_root / "hero" / slug
    folder.mkdir(mode=0o755, parents=True, exist_ok=True)
    stored = []
    for source, extension in ((rendered.mp4, "mp4"), (rendered.webm, "webm"), (rendered.poster, "jpg")):
        target = folder / f"{stem}.{extension}"
        shutil.copyfile(source, target)
        target.chmod(0o644)
        stored.append(f"hero/{slug}/{stem}.{extension}")
    return stored[0], stored[1], stored[2]


def _remove_files(public_root: Path, paths: list[str]) -> None:
    for path in paths:
        (public_root / path).unlink(missing_ok=True)


def finish(db: Session, campaign_id: uuid.UUID, public_root: Path, original: Path) -> None:
    """Renders the original and stores the result; never raises (the failure is
    recorded on the campaign so Gestión shows it)."""
    c = _get(db, campaign_id)
    failure: str | None = None
    stored: tuple[str, str, str] | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="hero-") as tmp:
            stored = _store(public_root, c.slug, render(original, Path(tmp)))
    except ContentError as exc:
        failure = exc.message
    except subprocess.TimeoutExpired:
        failure = "El video tardó demasiado en procesarse. Usa uno más corto."
    except Exception:  # noqa: BLE001 - recorded on the campaign, never raised in the background
        failure = "No se pudo procesar el video."
    db.refresh(c)
    if failure and c.video_mp4:
        failure += " Se conserva el video anterior."
    old = [p for p in (c.video_mp4, c.video_webm, c.poster) if p]
    if stored is None:
        c.processing_status, c.processing_error = "error", failure
    else:
        c.video_mp4, c.video_webm, c.poster = stored
        c.processing_status, c.processing_error = "ready", None
    c.processing_started_at = None
    db.add(AuditEvent(occurred_at=func.clock_timestamp(), actor_type=AuditActorType.system,
                      entity_type="hero_campaign", entity_id=c.id,
                      action="hero.video_ready" if stored else "hero.video_failed",
                      result=AuditResult.success if stored else AuditResult.failure,
                      event_metadata={"slug": c.slug, **({"error": failure} if failure else {})}))
    db.commit()
    if stored is not None:
        _remove_files(public_root, [p for p in old if p not in stored])
    original.unlink(missing_ok=True)


def run_job(campaign_id: uuid.UUID, public_root: Path, original: Path) -> None:
    """Background entry point: its own database session."""
    from app.db.base import SessionLocal

    with SessionLocal() as db:
        finish(db, campaign_id, public_root, original)


# --- public ---------------------------------------------------------------------------------


def public_view(c: HeroCampaign, reason: str | None) -> dict[str, Any]:
    return {
        "slug": c.slug,
        "name": c.name,
        "reason": reason,
        "video": {"mp4": f"/media/{c.video_mp4}", "webm": f"/media/{c.video_webm}" if c.video_webm else None},
        "poster": f"/media/{c.poster}",
    }
