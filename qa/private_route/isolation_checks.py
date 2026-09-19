"""Lightweight public-isolation checks (not a secret scanner).

The private certificate data must not be reachable from the public surface:
the public artisans/pieces API and the frontend files public pages are built
from. Checked against the synthetic secrets of this run: raw tokens,
token_hash values, certificate UUIDs and the internal metadata canary, plus
private field names.
"""
from __future__ import annotations

import http.client
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from private_route.common import SECRETS, Report
from private_route.fixtures import UNPUBLISHED_PIECE_SLUG, REVOKED_PIECE_SLUG, Fixtures

FORBIDDEN_KEY = re.compile(r"token|certificate|nfc|physical_uid|authenticity|audit", re.I)
PRIVATE_LINK = re.compile(r"""(?:href|src|action)\s*=\s*["']/c(?:/|["'])""")
PRIVATE_NAMES = (b"token_hash", b"physical_uid", b"certificates/resolve")


def _get(api_origin: str, path: str) -> tuple[int, str]:
    parts = urlsplit(api_origin)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        connection.close()


def _keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


def _leaks(fx: Fixtures, text: str | bytes) -> list[str]:
    problems = [f"contains {placeholder}" for placeholder in SECRETS.find(text)]
    for label, value in fx.all_secret_strings():
        hit = value.encode() in text if isinstance(text, bytes) else value in text
        if hit and not label.startswith(("token:", "token_hash:")):  # those are covered by SECRETS
            problems.append(f"contains {label}")
    return problems


def check_public_api(report: Report, fx: Fixtures, api_origin: str) -> None:
    report.section("Public isolation: public API while active certificates exist")
    paths = [
        "/api/v1/artisans",
        f"/api/v1/artisans/{fx.artisan_slug}",
        "/api/v1/pieces",
        f"/api/v1/pieces/{fx.piece_slug}",  # has an ACTIVE certificate
        f"/api/v1/pieces/{REVOKED_PIECE_SLUG}",  # has a REVOKED certificate
    ]
    for path in paths:
        status, text = _get(api_origin, path)
        problems = _leaks(fx, text)
        if status != 200:
            problems.append(f"status {status}, expected 200")
        try:
            bad_keys = sorted({k for k in _keys(json.loads(text)) if FORBIDDEN_KEY.search(k)})
        except ValueError:
            bad_keys = []
            problems.append("response is not JSON")
        if bad_keys:
            problems.append(f"private-looking field names present: {', '.join(bad_keys)}")
        report.group(f"GET {path}: no token, token_hash, certificate id, canary or certificate/NFC fields", problems)

    status, _ = _get(api_origin, f"/api/v1/pieces/{UNPUBLISHED_PIECE_SLUG}")
    report.check(status == 404, "GET /pieces/{unpublished piece with an active certificate} -> 404", f"status={status}")
    _, listing = _get(api_origin, "/api/v1/pieces")
    report.check(UNPUBLISHED_PIECE_SLUG not in listing, "unpublished piece is absent from the public pieces list")


def public_html_files(frontend: Path) -> list[Path]:
    files = [frontend / "index.html"]
    for section in ("artesanos", "piezas", "_shell"):
        files += sorted((frontend / section).rglob("*.html"))
    return [f for f in files if f.is_file()]


def entity_content_problems(frontend: Path) -> list[str]:
    """F-08: the public API is the only source of entity content. The tree may
    hold exactly the two list pages and the two neutral shells: no per-slug
    page, and no entity name, card or slug attribute in any of the four."""
    from private_route.public_checks import entity_strings

    problems: list[str] = []
    for section in ("piezas", "artesanos"):
        extra = sorted(str(p.relative_to(frontend)) for p in (frontend / section).rglob("*") if p.is_file() and p != frontend / section / "index.html")
        problems += [f"{e}: a per-slug page must not exist (the API decides what is published)" for e in extra]
    expected = {"piezas/index.html", "artesanos/index.html", "_shell/pieza/index.html", "_shell/artesano/index.html"}
    for rel in sorted(expected):
        path = frontend / rel
        if not path.is_file():
            problems.append(f"{rel} is missing")
            continue
        html = path.read_text(encoding="utf-8")
        problems += [f"{rel} contains {name!r}" for name in entity_strings() if name in html]
        if re.search(r"data-(piece|artisan)-slug|card__title|Demo|muestra|ficticio", html, re.I):
            problems.append(f"{rel} contains entity/fixture content (slug attribute, card, or demo text)")
    return problems


def check_static_frontend(report: Report, fx: Fixtures, frontend: Path) -> None:
    report.section("Public isolation: frontend static files behind public pages")
    datasets = sorted(str(p.relative_to(frontend)) for p in frontend.rglob("*.json"))
    report.check(not datasets, "no static JSON datasets under frontend/ (ARCHITECTURE.md section 9)", ", ".join(datasets))

    pages = public_html_files(frontend)
    scripts = sorted((frontend / "assets" / "js").glob("*.js"))
    report.check(len(pages) == 5, f"found {len(pages)} public HTML pages to inspect (Home, two lists, two shells)")
    report.group("no per-slug page and no entity content in the list pages and shells (the API is the only source)", entity_content_problems(frontend))

    problems: list[str] = []
    for page in pages:
        data = page.read_bytes()
        rel = page.relative_to(frontend)
        if PRIVATE_LINK.search(data.decode("utf-8", "replace")):
            problems.append(f"{rel}: links to the private /c/ route")
        problems += [f"{rel}: {p}" for p in _leaks(fx, data)]
        problems += [f"{rel}: mentions {n.decode()}" for n in PRIVATE_NAMES if n in data]
    report.group("public HTML pages: no /c/ link, no certificate/NFC internals, no run secrets", problems)

    problems = []
    for script in scripts + [frontend / "_headers", frontend / "_redirects"]:
        problems += [f"{script.name}: {p}" for p in _leaks(fx, script.read_bytes())]
    report.group("frontend scripts and routing files: no run secrets", problems)


def run(report: Report, fx: Fixtures, api_origin: str, frontend_dir: Path) -> None:
    check_public_api(report, fx, api_origin)
    check_static_frontend(report, fx, frontend_dir)
