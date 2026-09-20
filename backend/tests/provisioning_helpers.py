"""Shared helpers for the provisioning CLI tests (issue N-09).

The CLI commits at each checkpoint (register+assign+issue, program, lock ...)
and opens separate sessions for its read-only preflight and self-check, so
these tests cannot use the rolled-back `db_session` fixture: they commit real
rows to the test database (guarded by conftest's APP_ENV=test + test-named
database check), give every row a per-test unique label, and delete exactly
those rows on teardown - the same approach as `concurrency_helpers.py`.
"""
from __future__ import annotations

import os
import pty
import random
import re
import select
import signal
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import delete, select as sa_select
from sqlalchemy.orm import Session

from app.cli.provision import OperatorAbort, run
from app.db.base import SessionLocal, engine
from app.models import Artisan, Piece
from app.models.certificate import Certificate
from app.models.enums import PublicationStatus
from app.models.nfc_tag import NfcTag
from app.services import provisioning as prov

EOF = object()  # scripted "the operator closed stdin / pressed Ctrl-C"

BACKEND_DIR = Path(__file__).resolve().parents[1]
_URL_RE = re.compile(r"https?://[^\s/]+/c/([A-Za-z0-9_-]{43})")

# A fixed 43-character Base64url canary standing in for a real token, so a
# leak assertion is exact. Never a real secret.
CANARY_TOKEN = ("LeakCanaryToken" * 3)[:43]
assert len(CANARY_TOKEN) == 43


class ScriptedTerminal:
    """Injected terminal: scripted answers, everything else recorded.

    `reveal` is the only channel that may carry the certificate URL, so it is
    recorded apart from `said`/`warned`/`prompts`: leak tests assert the
    secret is in `revealed` and nowhere else.
    """

    def __init__(self, answers=(), *, secret_ok: bool = True) -> None:
        self._answers = list(answers)
        self.secret_ok = secret_ok
        self.said: list[str] = []
        self.warned: list[str] = []
        self.prompts: list[str] = []
        self.revealed: list[list[str]] = []

    # -- Terminal protocol ---------------------------------------------------
    def say(self, text: str = "") -> None:
        self.said.append(text)

    def warn(self, text: str) -> None:
        self.warned.append(text)

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._answers:
            raise AssertionError(f"unscripted prompt: {prompt!r}")
        item = self._answers.pop(0)
        if item is EOF:
            raise OperatorAbort
        if isinstance(item, tuple):  # (expected prompt fragment, answer)
            fragment, answer = item
            assert fragment in prompt, f"expected {fragment!r} in prompt {prompt!r}"
            return answer
        if callable(item):  # side effect first (e.g. a competing writer), then the answer
            return item(prompt)
        return item

    def secret_channel_ok(self) -> bool:
        return self.secret_ok

    def reveal(self, lines, hide_prompt: str) -> None:
        self.revealed.append(list(lines))

    # -- observation ---------------------------------------------------------
    @property
    def visible_text(self) -> str:
        """Everything a non-reveal channel showed."""
        return "\n".join([*self.said, *self.warned, *self.prompts])

    @property
    def revealed_text(self) -> str:
        return "\n".join("\n".join(lines) for lines in self.revealed)

    def tokens(self) -> list[str]:
        return _URL_RE.findall(self.revealed_text)

    def urls(self) -> list[str]:
        return [m.group(0) for m in _URL_RE.finditer(self.revealed_text)]

    def unused_answers(self) -> int:
        return len(self._answers)


def issue_answers(code: str, uid: str, *, readback="s", tail=None, scan="s", production=False):
    """The full scripted conversation of a successful `issue`."""
    return [
        *(["production"] if production else []),
        code,
        uid,
        "s",
        f"EMITIR {code}",
        "",  # Enter: show the URL
        "e",
        readback,
        tail if tail is not None else uid[-5:],
        scan,
    ]


def rotate_answers(code, reason, *, tail, choice=None, new_uid=None, scan="s", production=False):
    """`choice` is 'm'/'n' when the CLI offers both; None when it forces a new tag."""
    answers = [*(["production"] if production else []), code, reason]
    if choice is not None:
        answers.append(choice)
    if new_uid is not None:
        answers += [new_uid, "s"]
    answers += [f"ROTAR {code}", "", "e", "s", tail, scan]
    return answers


def revoke_answers(code, reason, *, production=False):
    return [*(["production"] if production else []), code, reason, f"REVOCAR {code}"]


def lock_answers(code, uid, *, production=False):
    return [*(["production"] if production else []), code, "s", "s", "s", uid[-5:], f"BLOQUEAR {code}"]


def run_cli(argv, terminal: ScriptedTerminal, *, environment=None, session_factory=SessionLocal) -> int:
    return run(argv, terminal=terminal, session_factory=session_factory, environment=environment)


# A production-looking environment (no placeholder password) for the tests
# that exercise the production banner/URL; the session factory still points at
# the test database.
PRODUCTION_ENV = ("production", "postgresql://svc:not-a-placeholder@localhost:5432/artesanfc")


class ProvisioningWorld:
    """Committed pieces + tracked UIDs for one test, deleted on teardown."""

    def __init__(self) -> None:
        self.label = uuid.uuid4().hex[:8]
        self._counter = 0
        self._piece_ids: list[uuid.UUID] = []
        self._artisan_ids: list[uuid.UUID] = []
        self._uids: list[str] = []

    def uid(self) -> str:
        uid = "04:" + ":".join(f"{random.randrange(256):02X}" for _ in range(6))
        self._uids.append(uid)
        return uid

    def piece(self, *, published: bool = True, artisan_published: bool = True) -> str:
        """A committed piece; returns its public_code."""
        self._counter += 1
        label = f"n09-{self.label}-{self._counter}"
        with Session(bind=engine) as s:
            artisan = Artisan(
                slug=f"artisan-{label}",
                full_name=f"Artesano {label}",
                publication_status=PublicationStatus.published if artisan_published else PublicationStatus.draft,
            )
            s.add(artisan)
            s.flush()
            piece = Piece(
                slug=label,
                public_code=label.upper(),
                artisan_id=artisan.id,
                name=f"Pieza {label}",
                publication_status=PublicationStatus.published if published else PublicationStatus.draft,
            )
            s.add(piece)
            s.commit()
            self._artisan_ids.append(artisan.id)
            self._piece_ids.append(piece.id)
            return piece.public_code

    # -- committed states through the real orchestration ---------------------------------
    def issued(self, code: str, *, program: bool = False, lock: bool = False):
        """Issue (and optionally program/lock) through `services.provisioning`,
        committing each step. Returns (raw_token, tag_uid, tag_id, cert_id)."""
        uid = self.uid()
        with Session(bind=engine) as s:
            result = prov.execute_issue(s, code, uid, operator="fixture")
            s.commit()
        if program or lock:
            with Session(bind=engine) as s:
                prov.execute_program(s, code, result.tag_id, operator="fixture")
                s.commit()
        if lock:
            with Session(bind=engine) as s:
                prov.execute_lock(s, code, operator="fixture")
                s.commit()
        return result.raw_token, uid, result.tag_id, result.certificate_id

    # -- observation ---------------------------------------------------------------------
    def piece_id(self, code: str) -> uuid.UUID:
        with Session(bind=engine) as s:
            return s.execute(sa_select(Piece.id).where(Piece.public_code == code)).scalar_one()

    def certificates(self, code: str) -> list[Certificate]:
        with Session(bind=engine) as s:
            rows = s.execute(
                sa_select(Certificate).where(Certificate.piece_id == self.piece_id(code)).order_by(Certificate.created_at)
            ).scalars().all()
            s.expunge_all()
            return list(rows)

    def tags(self, code: str) -> list[NfcTag]:
        with Session(bind=engine) as s:
            rows = s.execute(
                sa_select(NfcTag).where(NfcTag.piece_id == self.piece_id(code)).order_by(NfcTag.created_at)
            ).scalars().all()
            s.expunge_all()
            return list(rows)

    def tags_with_uid(self, uid: str) -> list[NfcTag]:
        with Session(bind=engine) as s:
            rows = s.execute(sa_select(NfcTag).where(NfcTag.physical_uid == uid)).scalars().all()
            s.expunge_all()
            return list(rows)

    def resolves(self, raw_token: str, code: str) -> bool:
        with Session(bind=engine) as s:
            return prov.verify_token_resolves(s, raw_token, code)

    def state(self, code: str):
        with Session(bind=engine) as s:
            return prov.load_piece_state(s, code)

    def cleanup(self) -> None:
        with Session(bind=engine) as s:
            s.execute(delete(Certificate).where(Certificate.piece_id.in_(self._piece_ids)))
            s.execute(delete(NfcTag).where(NfcTag.piece_id.in_(self._piece_ids)))
            if self._uids:
                s.execute(delete(NfcTag).where(NfcTag.physical_uid.in_(self._uids)))
            s.execute(delete(Piece).where(Piece.id.in_(self._piece_ids)))
            s.execute(delete(Artisan).where(Artisan.id.in_(self._artisan_ids)))
            s.commit()


@pytest.fixture()
def world():
    w = ProvisioningWorld()
    try:
        yield w
    finally:
        w.cleanup()


# --- real pseudo-terminal (Linux) ---------------------------------------------------------


class PtySession:
    """Runs a command as a child of a real pseudo-terminal, so the CLI sees
    genuine TTYs on stdin/stdout/stderr."""

    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # child
            os.chdir(BACKEND_DIR)
            os.execvpe(argv[0], argv, env)
        self.buffer = ""
        self.pos = 0

    def _fill(self, timeout: float) -> bool:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return True
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            return False
        if not data:
            return False
        self.buffer += data.decode("utf-8", "replace")
        return True

    def expect(self, needle: str, timeout: float = 30.0) -> str:
        """Everything from the last match up to and including `needle`."""
        deadline = time.monotonic() + timeout
        while True:
            found = self.buffer.find(needle, self.pos)
            if found != -1:
                start, self.pos = self.pos, found + len(needle)
                return self.buffer[start : self.pos]
            if time.monotonic() > deadline or not self._fill(0.2):
                raise AssertionError(f"never saw {needle!r}; tail of output: {self.buffer[-400:]!r}")

    def send(self, line: str) -> None:
        os.write(self.fd, (line + "\n").encode("utf-8"))

    def finish(self, timeout: float = 30.0) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self._fill(0.2):
            pass
        _, status = os.waitpid(self.pid, 0)
        return os.waitstatus_to_exitcode(status)

    def close(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def call_and_capture(fn: Callable[[], object]):
    """(result, exception) - for asserting on what a failure carries."""
    try:
        return fn(), None
    except BaseException as exc:  # noqa: BLE001
        return None, exc
