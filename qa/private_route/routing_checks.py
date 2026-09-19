"""HTTP-level proof of the production-like routing, without a browser.

`/c/{token}` must be answered by the certificate shell itself (status 200,
no redirect), never by the Home page: that was blocker B1
(docs/SPRINT_4.md section 12). Every request here goes straight to the
frontend server under test, whichever kind it is (built-in or wrangler).
"""
from __future__ import annotations

import http.client
import re
from urllib.parse import urlsplit

from private_route.common import Report
from private_route.fixtures import Fixtures

SHELL_TITLE = "ArtesaNFC — Certificado"


def http_get(base_url: str, path: str) -> tuple[int, dict[str, str], bytes]:
    parts = urlsplit(base_url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        connection.request("GET", path, headers={"Accept": "text/html"})
        response = connection.getresponse()
        body = response.read()
        headers = {name.lower(): value for name, value in response.getheaders()}
        return response.status, headers, body
    finally:
        connection.close()


def _title(body: bytes) -> str:
    match = re.search(rb"<title>(.*?)</title>", body, re.S)
    return match.group(1).decode("utf-8", "replace").strip() if match else ""


def shell_problems(status: int, headers: dict[str, str], body: bytes) -> list[str]:
    problems: list[str] = []
    if status != 200:
        problems.append(f"status {status}, expected 200 (a redirect or error is not a rewrite)")
    if _title(body) != SHELL_TITLE:
        problems.append(f"title is '{_title(body)}', expected '{SHELL_TITLE}' (Home served instead of the shell?)")
    if b'id="hero"' in body or b"id='hero'" in body:
        problems.append("response contains the Home #hero section")
    if b'id="cert-loading"' not in body or b'id="cert-authentic"' not in body:
        problems.append("response is missing the certificate state containers")
    if "no-store" not in headers.get("cache-control", "").lower():
        problems.append("Cache-Control does not contain no-store")
    if "noindex" not in headers.get("x-robots-tag", "").lower():
        problems.append("X-Robots-Tag does not contain noindex")
    policy = [p.strip().lower() for p in headers.get("referrer-policy", "").split(",") if p.strip()]
    if not policy or policy[-1] != "no-referrer":
        problems.append(f"effective Referrer-Policy is '{headers.get('referrer-policy', '')}', expected to end in no-referrer")
    return problems


def run(report: Report, fx: Fixtures, frontend_url: str, server_kind: str) -> None:
    report.section(f"Routing through the {server_kind} frontend server (HTTP, no redirect followed)")
    valid = fx.tokens["valid"]

    for label, path in [
        ("/c/{token}", f"/c/{valid}"),
        ("/c/{token}?x=1", f"/c/{valid}?x=1"),
        ("/c/", "/c/"),
        ("/c/foo/bar", "/c/foo/bar"),
    ]:
        report.group(f"GET {label} -> 200 certificate shell with private headers", shell_problems(*http_get(frontend_url, path)))

    status, headers, _ = http_get(frontend_url, "/c")
    location = urlsplit(headers.get("location", "")).path
    report.check(
        status in (301, 302, 307, 308) and location == "/c/",
        "GET /c -> redirect to /c/",
        f"status={status} location={location}",
    )

    for path in ("/", f"/piezas/{fx.piece_slug}/", f"/artesanos/{fx.artisan_slug}/"):
        status, _, body = http_get(frontend_url, path)
        report.check(
            status == 200 and _title(body) != SHELL_TITLE and _title(body) != "",
            f"GET {path} is still its own public page (not captured by the private rewrite)",
            f"status={status} title={_title(body)!r}",
        )
    for path, expected in (("/assets/css/tokens.css", "text/css"), ("/assets/js/hydrate-certificate.js", "javascript")):
        status, headers, _ = http_get(frontend_url, path)
        report.check(
            status == 200 and expected in headers.get("content-type", ""),
            f"GET {path} is served as a real asset ({expected})",
            f"status={status} type={headers.get('content-type')}",
        )
