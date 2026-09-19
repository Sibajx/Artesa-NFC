"""Shared concurrency/error primitives for the certificate and NFC lifecycle
services (audit findings F-03, F-09, F-10).

Two problems are solved here once instead of per service:

* Stale in-memory state. A caller-held ORM object may be older than the row
  it maps to. `snapshot_and_expire` + `lock_and_reload` replace the object's
  lifecycle columns with the persisted values under `SELECT ... FOR UPDATE`
  before any transition is validated, so a transition is always decided on
  the state the database actually holds at mutation time.
* Raw database errors. `run_in_savepoint` runs a transition in a SAVEPOINT
  (the outer session stays usable) and translates the database failures that
  are expected under contention into lifecycle errors that never carry
  PostgreSQL's DETAIL text. PostgreSQL embeds row values in DETAIL — for
  `certificate` that includes `token_hash` (docs/SECURITY.md sections 3 and
  13) — so nothing from the underlying exception is copied into, or chained
  to, the error the caller sees.

Callers own the commit; nothing here commits.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar

from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

T = TypeVar("T")

# SQLSTATEs (PostgreSQL appendix A) treated as "lost a race", never parsed
# from message text: deadlock_detected, lock_not_available.
_CONFLICT_SQLSTATES = frozenset({"40P01", "55P03"})

# Constraint names and SQLSTATEs are only ever echoed if they look like the
# schema identifiers/codes they are, so nothing else can ride along.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]{1,63}$")
_SQLSTATE_RE = re.compile(r"^[0-9A-Z]{5}$")

_CONTENTION_MESSAGE = (
    "The lifecycle operation conflicted with a concurrent transaction; "
    "reload the current state and retry."
)


class LifecycleError(Exception):
    """Root of every lifecycle service error (certificate and NFC)."""


class LifecycleConflict(LifecycleError):
    """A concurrent transaction changed the lifecycle underneath the caller:
    the caller's handle was stale, or the caller lost a race. Reload the
    current state and decide again. Distinct from an invalid transition
    (the request is wrong for the state the caller already knew) and from a
    genuinely missing record."""


class LifecycleIntegrityError(LifecycleError):
    """A database constraint failed in a way this service does not treat as a
    known race (including a `token_hash` collision). The message carries at
    most the constraint name and SQLSTATE — never the database's DETAIL, row
    values, a token or a token hash. Not safe to retry blindly."""

    def __init__(
        self, message: str, *, constraint: str | None = None, sqlstate: str | None = None
    ) -> None:
        super().__init__(message)
        self.constraint = constraint
        self.sqlstate = sqlstate


def _orig(exc: BaseException) -> Any:
    return getattr(exc, "orig", None)


def _sqlstate(exc: BaseException) -> str | None:
    orig = _orig(exc)
    value = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return value if isinstance(value, str) and _SQLSTATE_RE.fullmatch(value) else None


def _constraint_name(exc: BaseException) -> str | None:
    diag = getattr(_orig(exc), "diag", None)
    value = getattr(diag, "constraint_name", None)
    return value if isinstance(value, str) and _IDENTIFIER_RE.fullmatch(value) else None


def _integrity_message(constraint: str | None, sqlstate: str | None) -> str:
    detail = ", ".join(
        part
        for part in (
            f"constraint '{constraint}'" if constraint else None,
            f"SQLSTATE {sqlstate}" if sqlstate else None,
        )
        if part
    )
    return "Unexpected database constraint failure" + (f" ({detail})." if detail else ".")


def run_in_savepoint(
    db: Session,
    work: Callable[[], T],
    *,
    conflict: Callable[[str], LifecycleError],
    integrity: Callable[..., LifecycleIntegrityError],
    known: Mapping[str, Callable[[], LifecycleError]] | None = None,
) -> T:
    """Run ``work`` inside ``Session.begin_nested()`` and translate database
    failures into lifecycle errors.

    * ``IntegrityError`` whose constraint name is in ``known`` -> the error
      that factory builds (e.g. the partial unique index of "one active row
      per piece" -> the matching already-active error).
    * any other ``IntegrityError`` -> ``integrity(...)``.
    * ``OperationalError`` with SQLSTATE 40P01/55P03 -> ``conflict(...)``.
    * everything else, including the lifecycle errors ``work`` raises itself,
      propagates untouched.

    The translated error is raised *after* the ``except`` block, not inside
    it, so it has neither ``__cause__`` nor ``__context__``: the database
    exception (and the row values in its DETAIL) is unreachable from it.
    ``raise ... from None`` would only hide the context from formatting.
    """
    translated: LifecycleError | None = None
    try:
        with db.begin_nested():
            return work()
    except IntegrityError as exc:
        constraint, sqlstate = _constraint_name(exc), _sqlstate(exc)
        factory = known.get(constraint) if known and constraint else None
        translated = (
            factory()
            if factory is not None
            else integrity(
                _integrity_message(constraint, sqlstate), constraint=constraint, sqlstate=sqlstate
            )
        )
    except OperationalError as exc:
        if _sqlstate(exc) not in _CONFLICT_SQLSTATES:
            raise
        translated = conflict(_CONTENTION_MESSAGE)
    assert translated is not None
    raise translated


def snapshot_and_expire(db: Session, obj: Any, fields: Iterable[str]) -> dict[str, Any]:
    """Step 1 of lock-and-reload; call it BEFORE `run_in_savepoint`.

    Records what the caller's copy believed for each lifecycle field, then
    expires those attributes. Expiring discards any unflushed edit to them,
    so the flush `begin_nested()` performs when it opens the SAVEPOINT (and
    any later flush) can never persist a stale lifecycle value; edits to
    other attributes (e.g. `authenticity_metadata`, `notes`) are untouched
    and still flush normally. Pure in-memory: no SQL.

    A pending (never flushed) object has no persisted state to be stale
    against, so nothing is snapshotted for it.
    """
    state = sa_inspect(obj)
    if state.session is not db:
        raise ValueError(
            f"{type(obj).__name__} must be attached to the Session passed to the lifecycle service."
        )
    if not state.persistent:
        return {}

    names = tuple(fields)
    believed: dict[str, Any] = {}
    for name in names:
        history = state.attrs[name].history
        if history.deleted:
            believed[name] = history.deleted[0]
        elif history.unchanged:
            believed[name] = history.unchanged[0]
    db.expire(obj, list(names))
    return believed


def lock_and_reload(
    db: Session,
    obj: Any,
    model: type,
    fields: Iterable[str],
    believed: Mapping[str, Any],
    *,
    conflict: Callable[[str], LifecycleError],
) -> bool:
    """Step 2; call it inside the SAVEPOINT, before validating anything.

    `SELECT <lifecycle columns> ... FOR UPDATE` on the object's row, then
    loads exactly those columns into the object without recording history
    (nothing is dirty afterwards). Under READ COMMITTED a waiter re-reads the
    winner's committed row after the lock is granted.

    Returns True when the caller's copy was stale (its believed value of any
    lifecycle field differs from the persisted one). Raises ``conflict`` if
    the row no longer exists.
    """
    names = tuple(fields)
    identity = sa_inspect(obj).identity
    if identity is None:  # pending objects were inserted by begin_nested()'s flush
        raise ValueError(f"{type(obj).__name__} has no primary key after flush.")
    row = db.execute(
        select(*(getattr(model, name) for name in names))
        .where(model.id == identity[0])
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise conflict(f"{model.__name__} {identity[0]} no longer exists.")

    stale = False
    for name, fresh in zip(names, row):
        if name in believed and believed[name] != fresh:
            stale = True
        set_committed_value(obj, name, fresh)
    return stale
