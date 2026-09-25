"""HTTP-level proof of the production-like routing, without a browser.

`/c/{token}` must be answered by the certificate shell itself (status 200,
no redirect), never by the Home page: that was blocker B1
(docs/SPRINT_4.md section 12). Every request here goes straight to the
frontend server under test, whichever kind it is (built-in or wrangler).

F-08 adds ROUTE_MATRIX: ONE table of expected routing outcomes, checked
against (a) the built-in server's model of Pages and (b) the live frontend
server. Run with QA_SERVER=wrangler, (b) is real Wrangler and (a) vs (b) is an
explicit model-versus-Wrangler comparison on the real frontend tree.
"""
from __future__ import annotations

import http.client
import re
from pathlib import Path
from urllib.parse import urlsplit

from private_route.common import Report
from private_route.fixtures import Fixtures
from private_route.static_server import Site

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


HOME = ("file", "index.html")
PIEZA_SHELL = ("file", "_shell/pieza/index.html")
ARTESANO_SHELL = ("file", "_shell/artesano/index.html")
CERTIFICATE_SHELL = ("file", "c/index.html")

# path -> ("file", path relative to frontend/) | ("redirect", status, location).
# Expected outcomes on Wrangler 4.135.0 with the real frontend/_redirects.
ROUTE_MATRIX: list[tuple[str, tuple]] = [
    # private certificate route: unchanged by F-08
    ("/c/", CERTIFICATE_SHELL),
    ("/c/qa-token", CERTIFICATE_SHELL),
    ("/c/qa-a/qa-b", CERTIFICATE_SHELL),
    ("/c", ("redirect", 308, "/c/")),
    # list pages are NOT captured by the entity rewrites
    ("/piezas/", ("file", "piezas/index.html")),
    ("/piezas", ("redirect", 308, "/piezas/")),
    ("/artesanos/", ("file", "artesanos/index.html")),
    ("/artesanos", ("redirect", 308, "/artesanos/")),
    # exactly one slug -> the neutral shell, whether or not that slug exists
    ("/piezas/vasija-demo-01/", PIEZA_SHELL),
    ("/piezas/vasija-demo-01", PIEZA_SHELL),
    ("/piezas/qa-does-not-exist/", PIEZA_SHELL),
    ("/piezas/qa-does-not-exist", PIEZA_SHELL),
    ("/piezas/qa-does-not-exist/?x=1", PIEZA_SHELL),
    ("/piezas/qa%2Fslash", PIEZA_SHELL),
    ("/artesanos/artesano-demo-01/", ARTESANO_SHELL),
    ("/artesanos/artesano-demo-01", ARTESANO_SHELL),
    ("/artesanos/qa-does-not-exist/", ARTESANO_SHELL),
    ("/artesanos/qa-does-not-exist", ARTESANO_SHELL),
    # a 200 rewrite wins over an EXISTING file: /piezas/index.html is a real
    # file (the list page) and is still captured by /piezas/:slug. The former
    # model ("rewrite only when no asset exists") got this wrong.
    ("/piezas/index.html", PIEZA_SHELL),
    ("/artesanos/index.html", ARTESANO_SHELL),
    # nested paths are outside F-08: current behaviour (SPA fallback to Home)
    ("/piezas/qa-a/qa-b/", HOME),
    ("/piezas/qa-a/qa-b", HOME),
    ("/artesanos/qa-a/qa-b/", HOME),
    ("/artesanos/qa-a/qa-b", HOME),
    # the shells are reachable directly; they hold no entity
    ("/_shell/pieza/", PIEZA_SHELL),
    ("/_shell/pieza", ("redirect", 308, "/_shell/pieza/")),
    ("/_shell/artesano/", ARTESANO_SHELL),
    # everything else keeps its behaviour
    ("/nosotros", HOME),
    ("/assets/js/api.js", ("file", "assets/js/api.js")),
    ("/assets/css/tokens.css", ("file", "assets/css/tokens.css")),
]


def _model_outcome(site: Site, request_path: str) -> tuple:
    path, _, query = request_path.partition("?")
    kind, value = site.resolve(path, query)
    if kind == "redirect":
        status, location = value  # type: ignore[misc]
        return ("redirect", status, location.split("?", 1)[0])
    return ("file", Path(value).relative_to(site.root).as_posix())  # type: ignore[arg-type]


def matrix_problems_model(site: Site) -> list[str]:
    """ROUTE_MATRIX against the built-in server's model of Pages."""
    problems = []
    for path, expected in ROUTE_MATRIX:
        actual = _model_outcome(site, path)
        if actual != expected:
            problems.append(f"{path}: model says {actual}, expected {expected}")
    return problems


def matrix_problems_live(frontend_url: str, frontend_dir: Path) -> list[str]:
    """ROUTE_MATRIX against the frontend server actually running (built-in or
    Wrangler). Nothing is followed: a redirect is a redirect."""
    problems = []
    for path, expected in ROUTE_MATRIX:
        status, headers, body = http_get(frontend_url, path)
        if expected[0] == "redirect":
            location = urlsplit(headers.get("location", "")).path
            if (status, location) != (expected[1], expected[2]):
                problems.append(f"{path}: got {status} location={location!r}, expected {expected[1]} location={expected[2]!r}")
            continue
        wanted = (frontend_dir / expected[1]).read_bytes()
        if status != 200:
            problems.append(f"{path}: got status {status}, expected 200 serving {expected[1]}")
        elif body != wanted:
            problems.append(f"{path}: served body ({_title(body)!r}) is not {expected[1]} ({_title(wanted)!r})")
    return problems


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


def run(report: Report, fx: Fixtures, frontend_url: str, frontend_dir: Path, server_kind: str) -> None:
    report.section(f"Routing through the {server_kind} frontend server (HTTP, no redirect followed)")
    valid = fx.tokens["valid"]

    report.group(
        f"route matrix ({len(ROUTE_MATRIX)} paths): built-in Pages model == expected",
        matrix_problems_model(Site(frontend_dir)),
    )
    report.group(
        f"route matrix ({len(ROUTE_MATRIX)} paths): live {server_kind} server == expected"
        + (" (real Wrangler; model and Wrangler agree on every path)" if server_kind == "wrangler" else ""),
        matrix_problems_live(frontend_url, frontend_dir),
    )

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

    for path in ("/", "/piezas/", "/artesanos/", f"/piezas/{fx.piece_slug}/", f"/artesanos/{fx.artisan_slug}/"):
        status, headers, body = http_get(frontend_url, path)
        report.check(
            status == 200 and _title(body) != SHELL_TITLE and _title(body) != "" and "no-store" not in headers.get("cache-control", ""),
            f"GET {path} is a public page (not captured by the private rewrite, no private headers)",
            f"status={status} title={_title(body)!r}",
        )
    for path, expected in (("/assets/css/tokens.css", "text/css"), ("/assets/js/hydrate-certificate.js", "javascript")):
        status, headers, _ = http_get(frontend_url, path)
        report.check(
            status == 200 and expected in headers.get("content-type", ""),
            f"GET {path} is served as a real asset ({expected})",
            f"status={status} type={headers.get('content-type')}",
        )
