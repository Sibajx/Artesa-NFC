"""Body-size limit on POST /api/v1/certificates/resolve (audit finding N-03,
docs/SECURITY.md section 5.5, docs/OPERATIONS.md).

These drive the real `app` (CORS + no-store + the limit + FastAPI) at the ASGI
level, with hand-built messages, because the point is *how much of the body was
read*: Content-Length must be a fast path only, a body with no Content-Length
or delivered in chunks must be counted as it arrives, and once the limit is
passed the rest must never be consumed. A real Uvicorn over real sockets is
covered in tests/test_edge_uvicorn.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from itertools import count

import pytest

from app.core.config import get_settings
from app.main import ResolveBodySizeLimitMiddleware, app

RESOLVE = "/api/v1/certificates/resolve"
LIMIT = 1024
ALLOWED_ORIGIN = get_settings().cors_allowed_origins_list[0]
TOO_LARGE = {"error": {"code": "payload_too_large", "message": "The request body is too large."}}
UNAVAILABLE = {"authenticity": {"status": "unavailable"}}
CANARY = "CANARY_PAYLOAD_MUST_NOT_BE_REFLECTED"


@dataclass
class Outcome:
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    receive_calls: int = 0
    app_invoked: bool = False

    @property
    def json(self):
        return json.loads(self.body)


def _pad_json(total: int, token: str = "abc") -> bytes:
    """A valid JSON body of exactly `total` bytes (trailing whitespace)."""
    core = json.dumps({"token": token}).encode()
    assert len(core) <= total
    return core + b" " * (total - len(core))


def call(asgi_app, *, method="POST", path=RESOLVE, chunks=(b"",), headers=None, content_length="auto", stream=None):
    """Run one request through `asgi_app`. `chunks` is the body split into
    messages; `stream` (an iterator of byte chunks) replaces it for an
    unbounded body. Returns what was sent and how many times the server side
    asked for more body."""
    raw_headers = {k.lower(): v for k, v in (headers or {}).items()}
    if content_length == "auto":
        if stream is None:
            raw_headers.setdefault("content-length", str(sum(len(c) for c in chunks)))
    elif content_length is not None:
        raw_headers["content-length"] = str(content_length)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in raw_headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }

    outcome = Outcome()
    source = iter(stream) if stream is not None else iter(chunks)
    pending = next(source, None)

    async def receive():
        nonlocal pending
        outcome.receive_calls += 1
        if pending is None:
            return {"type": "http.disconnect"}
        current = pending
        pending = next(source, None)
        return {"type": "http.request", "body": current, "more_body": pending is not None}

    async def send(message):
        if message["type"] == "http.response.start":
            outcome.status = message["status"]
            outcome.headers = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            outcome.body += message.get("body", b"")

    asyncio.run(asgi_app(scope, receive, send))
    return outcome


# --- Over the limit -----------------------------------------------------------------


def test_declared_content_length_over_the_limit_is_413_without_reading_any_body():
    outcome = call(app, chunks=(b"x" * 10,), content_length=LIMIT + 1)
    assert outcome.status == 413
    assert outcome.json == TOO_LARGE
    assert outcome.receive_calls == 0  # Content-Length fast path: no body read at all


def test_body_over_the_limit_with_matching_content_length_is_413():
    outcome = call(app, chunks=(b"x" * (LIMIT + 1),))
    assert outcome.status == 413 and outcome.json == TOO_LARGE


def test_body_over_the_limit_without_content_length_is_413():
    outcome = call(app, chunks=(b"x" * (LIMIT + 1),), content_length=None)
    assert outcome.status == 413 and outcome.json == TOO_LARGE


def test_a_false_content_length_is_not_trusted():
    # Declares a small body, sends a large one.
    outcome = call(app, chunks=(b"x" * 5000,), content_length=10)
    assert outcome.status == 413 and outcome.json == TOO_LARGE


def test_chunked_body_over_the_limit_is_413():
    outcome = call(app, chunks=(b"x" * 400, b"x" * 400, b"x" * 400), content_length=None)
    assert outcome.status == 413 and outcome.json == TOO_LARGE
    assert outcome.receive_calls == 3  # stopped as soon as 1200 > 1024, nothing more asked for


def test_a_huge_stream_is_cut_off_after_the_limit_is_passed():
    chunks_served = count()

    def huge():
        # ~10 MB if nobody stops reading. Bounded on purpose: if the limit ever
        # broke, this must fail on the assertions below instead of hanging.
        for _ in range(20_000):
            next(chunks_served)
            yield b"x" * 512

    outcome = call(app, stream=huge(), content_length=None)
    assert outcome.status == 413 and outcome.json == TOO_LARGE
    # 512, 1024 (still allowed), 1536 (over): the third chunk trips it. One
    # more may have been prefetched by the iterator; the point is that it is
    # bounded, not consumed.
    assert outcome.receive_calls == 3
    assert next(chunks_served) <= 4


def test_the_inner_application_is_never_invoked_for_an_oversized_body():
    invoked = []

    async def inner(scope, receive, send):
        invoked.append(True)

    outcome = call(ResolveBodySizeLimitMiddleware(inner), chunks=(b"x" * (LIMIT + 1),))
    assert outcome.status == 413
    assert invoked == []


def test_non_numeric_content_length_falls_back_to_counting():
    outcome = call(app, chunks=(b"x" * (LIMIT + 1),), content_length="12abc")
    assert outcome.status == 413


# --- At and under the limit ------------------------------------------------------------


def test_a_body_of_exactly_the_limit_reaches_the_application():
    body = _pad_json(LIMIT)
    assert len(body) == LIMIT
    outcome = call(app, chunks=(body,))
    assert outcome.status == 200
    assert outcome.json == UNAVAILABLE


def test_a_body_of_exactly_the_limit_without_content_length_reaches_the_application():
    outcome = call(app, chunks=(_pad_json(LIMIT),), content_length=None)
    assert outcome.status == 200 and outcome.json == UNAVAILABLE


def test_a_chunked_body_of_exactly_the_limit_reaches_the_application_unchanged():
    body = _pad_json(LIMIT)
    outcome = call(app, chunks=(body[:400], body[400:800], body[800:]), content_length=None)
    assert outcome.status == 200 and outcome.json == UNAVAILABLE


def test_the_replayed_body_is_byte_identical():
    seen = []

    async def inner(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        seen.append(body)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    payload = bytes(range(256)) * 4  # 1024 bytes
    outcome = call(ResolveBodySizeLimitMiddleware(inner), chunks=(payload[:300], payload[300:]), content_length=None)
    assert outcome.status == 204
    assert seen == [payload]


def test_a_normal_token_request_still_works():
    outcome = call(app, chunks=(json.dumps({"token": "A" * 43}).encode(),))
    assert outcome.status == 200 and outcome.json == UNAVAILABLE


def test_an_empty_body_is_left_to_normal_validation():
    outcome = call(app, chunks=(b"",), content_length=None)
    assert outcome.status == 422
    assert outcome.json["error"]["code"] == "validation_error"


def test_a_client_disconnect_before_any_body_is_passed_on():
    async def inner(scope, receive, send):
        message = await receive()
        assert message["type"] == "http.disconnect"
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": RESOLVE, "headers": []}
    asyncio.run(ResolveBodySizeLimitMiddleware(inner)(scope, receive, send))
    assert sent[0]["status"] == 204


# --- Scope: paths and methods -------------------------------------------------------------


def test_the_trailing_slash_form_is_limited_too():
    outcome = call(app, path=RESOLVE + "/", chunks=(b"x" * (LIMIT + 1),))
    assert outcome.status == 413 and outcome.json == TOO_LARGE


def test_the_trailing_slash_form_within_the_limit_keeps_the_normal_redirect():
    outcome = call(app, path=RESOLVE + "/", chunks=(_pad_json(LIMIT),))
    assert outcome.status == 307


def test_options_is_unchanged():
    # A real preflight is answered by the CORS layer.
    preflight = call(
        app,
        method="OPTIONS",
        chunks=(b"",),
        headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status == 200
    assert preflight.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    # A plain OPTIONS with an oversized body is not the limited request.
    plain = call(app, method="OPTIONS", chunks=(b"x" * 5000,))
    assert plain.status == 405


def test_get_is_unchanged():
    outcome = call(app, method="GET", chunks=(b"x" * 5000,))
    assert outcome.status == 405
    assert outcome.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("GET", "/api/v1/artisans", 200),
        ("POST", "/api/v1/certificates/other", 404),
        ("POST", "/health", 405),
        ("POST", "/api/v1/pieces", 405),
    ],
)
def test_other_endpoints_are_not_limited(method, path, expected):
    outcome = call(app, method=method, path=path, chunks=(b"x" * 5000,))
    assert outcome.status == expected  # never 413


def test_the_limit_is_this_middlewares_1024_bytes_by_default():
    assert ResolveBodySizeLimitMiddleware(lambda *a: None).max_body_bytes == LIMIT


# --- What the 413 carries -------------------------------------------------------------------


def test_the_413_is_no_store():
    outcome = call(app, chunks=(b"x" * (LIMIT + 1),))
    assert outcome.headers["cache-control"] == "no-store"


def test_the_413_carries_cors_for_an_allowed_origin():
    outcome = call(app, chunks=(b"x" * (LIMIT + 1),), headers={"Origin": ALLOWED_ORIGIN})
    assert outcome.status == 413
    assert outcome.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert outcome.headers["cache-control"] == "no-store"


def test_the_413_has_no_cors_for_a_disallowed_origin():
    outcome = call(app, chunks=(b"x" * (LIMIT + 1),), headers={"Origin": "https://evil.example"})
    assert outcome.status == 413
    assert "access-control-allow-origin" not in outcome.headers


def test_the_payload_is_not_reflected_in_the_response_or_the_logs(caplog):
    payload = json.dumps({"token": CANARY * 40}).encode()
    assert len(payload) > LIMIT
    with caplog.at_level(logging.DEBUG):
        outcome = call(app, chunks=(payload,))
    assert outcome.status == 413
    everything = outcome.body.decode() + str(outcome.headers) + caplog.text
    assert "CANARY" not in everything
