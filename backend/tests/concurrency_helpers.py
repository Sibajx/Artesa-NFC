"""Helpers for the multi-connection PostgreSQL lifecycle tests
(test_certificate_concurrency.py, test_nfc_tag_concurrency.py).

Unlike the rest of the suite these tests cannot use the rolled-back
`db_session` fixture: a race needs several independent connections, and
another connection only sees *committed* rows. They therefore commit real
rows to the test database (guarded by conftest's APP_ENV=test + test-named
database check), tag every row with a per-test unique id, and delete exactly
those rows on teardown.

Races are synchronized on database facts, never on sleeps: the main thread
holds a transaction open, a worker thread runs the competing call, and
`wait_until_blocked` polls `pg_stat_activity` until the worker's backend is
really waiting on a lock before the main thread lets go.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db.base import engine
from app.models import Artisan, Piece
from app.models.certificate import Certificate, CertificateStatus
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.services.certificates import activate_certificate
from app.services.nfc_tags import assign_nfc_tag, lock_nfc_tag, program_nfc_tag

_JOIN_TIMEOUT = 20.0
_BLOCK_TIMEOUT = 15.0


class Worker:
    """Runs ``fn(session)`` on its own thread and Session (= own connection)
    and records the outcome. The backend pid is read before the thread
    starts so the main thread can wait on it deterministically."""

    def __init__(self, session: Session, fn: Callable[[Session], Any]) -> None:
        self.session = session
        self.pid: int = session.execute(text("select pg_backend_pid()")).scalar_one()
        self.result: Any = None
        self.exc: BaseException | None = None
        self._fn = fn
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.result = self._fn(self.session)
        except BaseException as exc:  # noqa: BLE001 - outcome is asserted by the test
            self.exc = exc

    def start(self) -> "Worker":
        self._thread.start()
        return self

    def wait_until_blocked(self) -> None:
        deadline = time.monotonic() + _BLOCK_TIMEOUT
        with engine.connect() as probe:
            while time.monotonic() < deadline:
                waiting = probe.execute(
                    text("select wait_event_type from pg_stat_activity where pid = :pid"),
                    {"pid": self.pid},
                ).scalar()
                if waiting == "Lock":
                    return
                if not self._thread.is_alive():
                    pytest.fail("worker finished without ever blocking on a lock")
                time.sleep(0.005)
        pytest.fail("worker never blocked on a lock")

    def join(self) -> "Worker":
        self._thread.join(_JOIN_TIMEOUT)
        assert not self._thread.is_alive(), "worker did not finish (unreleased lock?)"
        return self


class World:
    """Committed test data + tracked sessions for one test."""

    def __init__(self) -> None:
        self.tag_id = uuid.uuid4().hex[:10]
        self._sessions: list[Session] = []
        self._piece_ids: list[uuid.UUID] = []
        self._artisan_ids: list[uuid.UUID] = []
        self._tag_ids: list[uuid.UUID] = []
        self._counter = 0

    # -- sessions ---------------------------------------------------------
    def session(self) -> Session:
        session = Session(bind=engine, autoflush=False)
        self._sessions.append(session)
        return session

    def worker(self, fn: Callable[[Session], Any], *, session: Session | None = None) -> Worker:
        """`session` lets a test hand over a session that already holds ORM
        objects loaded earlier - i.e. state that is about to go stale."""
        return Worker(session if session is not None else self.session(), fn)

    # -- committed rows ---------------------------------------------------
    def _next(self) -> str:
        self._counter += 1
        return f"conc-{self.tag_id}-{self._counter}"

    def piece(self) -> uuid.UUID:
        label = self._next()
        with Session(bind=engine) as s:
            artisan = Artisan(slug=f"artisan-{label}", full_name="Concurrency")
            s.add(artisan)
            s.flush()
            piece = Piece(slug=label, public_code=f"PC-{label}", artisan_id=artisan.id, name="Concurrency")
            s.add(piece)
            s.commit()
            self._artisan_ids.append(artisan.id)
            self._piece_ids.append(piece.id)
            return piece.id

    def draft_certificate(self, piece_id: uuid.UUID) -> uuid.UUID:
        with Session(bind=engine) as s:
            cert = Certificate(piece_id=piece_id, status=CertificateStatus.draft)
            s.add(cert)
            s.commit()
            return cert.id

    def active_certificate(self, piece_id: uuid.UUID) -> tuple[uuid.UUID, str]:
        """Committed active certificate issued through the service; returns
        (certificate id, raw token)."""
        cert_id = self.draft_certificate(piece_id)
        with Session(bind=engine) as s:
            result = activate_certificate(s, s.get(Certificate, cert_id))
            s.commit()
            return cert_id, result.raw_token

    def tag(self, piece_id: uuid.UUID | None = None) -> uuid.UUID:
        """Committed `available` tag, assigned to `piece_id` when given
        (assignment through the service)."""
        with Session(bind=engine) as s:
            tag = NfcTag(chip_model="NTAG213", status=NfcTagStatus.available, notes=self._next())
            s.add(tag)
            s.commit()
            self._tag_ids.append(tag.id)
            if piece_id is not None:
                assign_nfc_tag(s, tag, s.get(Piece, piece_id))
                s.commit()
            return tag.id

    def active_tag(self, piece_id: uuid.UUID, *, locked: bool = False) -> uuid.UUID:
        tag_id = self.tag(piece_id)
        with Session(bind=engine) as s:
            tag = s.get(NfcTag, tag_id)
            program_nfc_tag(s, tag)
            if locked:
                lock_nfc_tag(s, tag)
            s.commit()
        return tag_id

    # -- observation ------------------------------------------------------
    def certificates(self, piece_id: uuid.UUID) -> dict[uuid.UUID, Certificate]:
        with Session(bind=engine) as s:
            rows = s.execute(select(Certificate).where(Certificate.piece_id == piece_id)).scalars().all()
            s.expunge_all()
            return {row.id: row for row in rows}

    def tags(self, *tag_ids: uuid.UUID) -> dict[uuid.UUID, NfcTag]:
        with Session(bind=engine) as s:
            rows = s.execute(select(NfcTag).where(NfcTag.id.in_(tag_ids))).scalars().all()
            s.expunge_all()
            return {row.id: row for row in rows}

    def active_tag_count(self, piece_id: uuid.UUID) -> int:
        with Session(bind=engine) as s:
            return len(
                s.execute(
                    select(NfcTag.id).where(
                        NfcTag.piece_id == piece_id,
                        NfcTag.status.in_([NfcTagStatus.programmed, NfcTagStatus.locked]),
                    )
                ).all()
            )

    def active_certificate_count(self, piece_id: uuid.UUID) -> int:
        return sum(c.status == CertificateStatus.active for c in self.certificates(piece_id).values())

    # -- teardown ---------------------------------------------------------
    def cleanup(self) -> None:
        for session in self._sessions:
            try:
                session.rollback()
            finally:
                session.close()
        with Session(bind=engine) as s:
            # A leaked lock must fail the teardown loudly, not hang the suite.
            s.execute(text("SET LOCAL lock_timeout = '5s'"))
            s.execute(delete(Certificate).where(Certificate.piece_id.in_(self._piece_ids)))
            s.execute(delete(NfcTag).where(NfcTag.id.in_(self._tag_ids)))
            s.execute(delete(NfcTag).where(NfcTag.piece_id.in_(self._piece_ids)))
            s.execute(delete(Piece).where(Piece.id.in_(self._piece_ids)))
            s.execute(delete(Artisan).where(Artisan.id.in_(self._artisan_ids)))
            s.commit()


@pytest.fixture()
def world():
    w = World()
    try:
        yield w
    finally:
        w.cleanup()


def assert_no_exception_chain(exc: BaseException) -> None:
    """A translated lifecycle error must not reach the database exception
    (whose DETAIL can embed row values such as token_hash)."""
    assert exc.__cause__ is None
    assert exc.__context__ is None
