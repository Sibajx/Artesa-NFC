"""PEND-005 / PEND-025: a per-IP limit on the public ``GET`` endpoints and a
cooldown for sustained abuse (docs/SECURITY.md section 5.2).

Cloudflare's rate limiting stays the primary layer (docs/OPERATIONS.md section
9); this one is the origin's own backstop, for traffic that reaches it anyway.

- Sliding window: at most ``public_rate_limit_per_minute`` GET/HEAD requests
  per IP in 60 seconds under ``/api/v1/``; beyond that, ``429`` with
  ``Retry-After``.
- Cooldown: an IP that keeps sending while limited (``public_abuse_rejections``
  refused requests inside one minute) is refused outright for
  ``public_abuse_cooldown_seconds``, whatever it sends.

Scope: only ``GET``/``HEAD`` under ``/api/v1/``. The certificate POSTs keep
their own attempt limits (app/services/ownership.py); ``/health``, ``/media/``
and ``/api/admin/`` (behind Access) are never counted. The address is the one
Uvicorn resolves with ``--proxy-headers`` behind cloudflared.

State lives in the process: correct with the single worker production runs,
and it resets on restart. A limit of 0 turns the whole thing off.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings
from app.core.errors import error_response

_WINDOW = 60.0
_PUBLIC_PREFIX = "/api/v1/"
# Above this many tracked addresses the idle ones are swept on the next request.
_SWEEP_ABOVE = 10_000


@dataclass
class _IpState:
    hits: deque[float] = field(default_factory=deque)
    rejections: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0


class PublicRateLimiter:
    def __init__(self) -> None:
        self._states: dict[str, _IpState] = {}

    def reset(self) -> None:
        self._states.clear()

    def check(self, ip: str, now: float, *, limit: int, abuse_rejections: int, cooldown: float) -> float | None:
        """None when the request may go on; otherwise the seconds to wait."""
        if len(self._states) > _SWEEP_ABOVE:
            self._sweep(now, cooldown)
        state = self._states.setdefault(ip, _IpState())
        if state.blocked_until > now:
            return state.blocked_until - now

        cutoff = now - _WINDOW
        while state.hits and state.hits[0] <= cutoff:
            state.hits.popleft()
        while state.rejections and state.rejections[0] <= cutoff:
            state.rejections.popleft()

        if len(state.hits) < limit:
            state.hits.append(now)
            return None

        state.rejections.append(now)
        if len(state.rejections) >= abuse_rejections:
            state.blocked_until = now + cooldown
            state.hits.clear()
            state.rejections.clear()
            return cooldown
        return state.hits[0] + _WINDOW - now

    def _sweep(self, now: float, cooldown: float) -> None:
        cutoff = now - max(_WINDOW, cooldown)
        for ip in [
            ip for ip, s in self._states.items()
            if s.blocked_until <= now and (not s.hits or s.hits[-1] <= cutoff)
        ]:
            del self._states[ip]


limiter = PublicRateLimiter()


class PublicRateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in ("GET", "HEAD")
            or not scope["path"].startswith(_PUBLIC_PREFIX)
            or not scope.get("client")
        ):
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        if settings.public_rate_limit_per_minute == 0:
            await self.app(scope, receive, send)
            return

        wait = limiter.check(
            scope["client"][0],
            time.monotonic(),
            limit=settings.public_rate_limit_per_minute,
            abuse_rejections=settings.public_abuse_rejections,
            cooldown=settings.public_abuse_cooldown_seconds,
        )
        if wait is None:
            await self.app(scope, receive, send)
            return

        response = error_response(429)
        response.headers["Retry-After"] = str(max(1, math.ceil(wait)))
        response.headers["Cache-Control"] = "no-store"
        await response(scope, receive, send)
