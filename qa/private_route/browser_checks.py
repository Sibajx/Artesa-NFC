"""Chromium checks for /c/{token} (Playwright, headless).

Every case is asserted on FOUR layers so a harness that silently makes no API
request cannot pass (the audit found one: a redirect broke token extraction and
every case merely *looked* "unavailable"):

  1. navigation   status 200, not redirected, final URL == requested URL
  2. requests     exact number of resolve POSTs, exact URL (=> empty query),
                  JSON body with exactly the expected `token` field; ZERO API
                  requests for missing/malformed tokens
  3. response     status, no-store, authenticity status, no secret echoed
  4. UI + privacy exactly one state visible, expected content, and the token
                  absent from every other request, the DOM and browser storage

No trace, video, screenshot or HAR is ever produced; contexts are
non-persistent. Third-party hosts are stubbed (fonts) or blocked, so the run
is hermetic and nothing leaves loopback.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from private_route.common import SECRETS, Report, scan_tree, scrub
from private_route.fixtures import Fixtures
from private_route.routing_checks import SHELL_TITLE

VIEWPORTS = {"desktop": {"width": 1280, "height": 800}, "mobile": {"width": 390, "height": 844}}
RESOLVE_PATH = "/api/v1/certificates/resolve"
LOOPBACK = {"127.0.0.1", "localhost"}
FONT_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com"}
STATE_ID = {"authentic": "cert-authentic", "unavailable": "cert-unavailable", "error": "cert-error"}
ALL_STATE_IDS = ("cert-nojs", "cert-loading", "cert-unavailable", "cert-error", "cert-authentic")
HEADING = {"unavailable": "Certificado no disponible", "error": "Verificación no disponible temporalmente"}
FINAL_STATE_JS = """() => ['cert-unavailable', 'cert-error', 'cert-authentic'].some(
    id => { const e = document.getElementById(id); return e && !e.hidden; })"""
BROWSER_STATE_JS = """async () => {
  const safe = async (fn) => { try { return await fn(); } catch (e) { return -1; } };
  return {
    local: localStorage.length, session: sessionStorage.length, cookie: document.cookie,
    name: window.name, history: JSON.stringify(history.state), referrer: document.referrer,
    idb: await safe(async () => (indexedDB.databases ? (await indexedDB.databases()).length : 0)),
    caches: await safe(async () => (window.caches ? (await caches.keys()).length : 0)),
    sw: await safe(async () => (navigator.serviceWorker ? (await navigator.serviceWorker.getRegistrations()).length : 0)),
  };
}"""


@dataclass(frozen=True)
class Case:
    name: str
    path: Callable[[Fixtures], str]
    state: str  # authentic | unavailable | error
    token: Callable[[Fixtures], str] | None  # raw token the POST must carry; None => zero API requests
    stub: str | None = None  # None (real API) | "abort" | "http500"
    converges: bool = False  # real 'unavailable' response must be identical to the other converging cases


def _tok(alias: str) -> Callable[[Fixtures], str]:
    return lambda fx: fx.tokens[alias]


WRONG_LENGTH = "A" * 20  # right alphabet, wrong length: reaches the API, converges on unavailable
CASES: list[Case] = [
    Case("valid token", lambda fx: f"/c/{fx.tokens['valid']}", "authentic", _tok("valid")),
    Case("invalid token", lambda fx: f"/c/{fx.tokens['invalid']}", "unavailable", _tok("invalid"), converges=True),
    Case("revoked certificate", lambda fx: f"/c/{fx.tokens['revoked']}", "unavailable", _tok("revoked"), converges=True),
    Case("active certificate on unpublished piece", lambda fx: f"/c/{fx.tokens['unpublished']}", "unavailable", _tok("unpublished"), converges=True),
    Case("well-formed alphabet, wrong length", lambda fx: f"/c/{WRONG_LENGTH}", "unavailable", lambda fx: WRONG_LENGTH, converges=True),
    Case("missing token (/c/)", lambda fx: "/c/", "unavailable", None),
    Case("token only in query (/c/?token=)", lambda fx: f"/c/?token={fx.tokens['valid']}", "unavailable", None),
    Case("malformed: dot in token", lambda fx: "/c/abc.def", "unavailable", None),
    Case("malformed: invalid percent-encoding", lambda fx: "/c/%E0%A4%A", "unavailable", None),
    Case("malformed: nested path", lambda fx: "/c/foo/bar", "unavailable", None),
    Case("malformed: 300 characters", lambda fx: "/c/" + "A" * 300, "unavailable", None),
    Case("malformed: encoded space", lambda fx: "/c/tok%20en", "unavailable", None),
    Case("malformed: real token + suffix", lambda fx: f"/c/{fx.tokens['valid']}.x", "unavailable", None),
    Case("malformed: real token + extra segment", lambda fx: f"/c/{fx.tokens['valid']}/extra", "unavailable", None),
    Case(
        "path token wins over query/fragment",
        lambda fx: f"/c/{fx.tokens['invalid']}?t={fx.tokens['valid']}#{fx.tokens['valid']}",
        "unavailable",
        _tok("invalid"),
    ),
    Case("transport failure (request aborted)", lambda fx: f"/c/{fx.tokens['valid']}", "error", _tok("valid"), stub="abort"),
    Case("transport failure (HTTP 500)", lambda fx: f"/c/{fx.tokens['valid']}", "error", _tok("valid"), stub="http500"),
]


CASE_TIMEOUT_SECONDS = 90


class _Watchdog:
    """Hard stop for a case. Playwright's sync API has calls without a timeout;
    if one ever blocks, the run must fail loudly instead of hanging. Exit code 2
    (harness error); the shell script's trap still cleans everything up."""

    def __init__(self, label: str) -> None:
        self._timer = threading.Timer(CASE_TIMEOUT_SECONDS, self._expire, args=(label,))
        self._timer.daemon = True

    @staticmethod
    def _expire(label: str) -> None:
        print(scrub(f"\nQA harness error: case '{label}' exceeded {CASE_TIMEOUT_SECONDS}s (a browser call blocked)"), file=sys.stderr, flush=True)
        os._exit(2)

    def __enter__(self):
        self._timer.start()
        return self

    def __exit__(self, *exc) -> None:
        self._timer.cancel()


def _flat(headers: dict[str, str]) -> str:
    return json.dumps(headers, sort_keys=True)


def _leak_problems(reqs: list, main_doc, resolve_req) -> list[str]:
    """The token may appear only in the main document URL and in the resolve
    POST body. Nowhere else: not in any other URL, not in any request header
    (Referer!), not in any other body."""
    problems: list[str] = []
    for r in reqs:
        label = f"{r.method} {r.resource_type} {scrub(r.url)}"
        if r is not main_doc and SECRETS.find(r.url):
            problems.append(f"secret in request URL: {label}")
        try:
            header_hits = SECRETS.find(_flat(r.all_headers()))
        except Exception:  # noqa: BLE001 - request already gone; nothing to inspect
            header_hits = []
        if header_hits:
            problems.append(f"secret in request headers ({', '.join(header_hits)}): {label}")
        if r is not resolve_req and r.post_data and SECRETS.find(r.post_data):
            problems.append(f"secret in a request body: {label}")
    return problems


def _authentic_problems(page, fx: Fixtures, body: dict) -> list[str]:
    problems: list[str] = []

    def text(selector: str) -> str:
        return (page.locator(selector).text_content() or "").strip()

    expected = {
        "#cert-piece-title": fx.piece_name,
        "#cert-artisan-name": fx.artisan_display_name,
        "#cert-artisan-location": fx.artisan_location,
        "#cert-version": "1",
        "#cert-notes": fx.notes,
    }
    for selector, value in expected.items():
        if text(selector) != value:
            problems.append(f"{selector} is '{text(selector)}', expected '{value}'")
    issued = text("#cert-issued-at")
    if not re.search(r"\d{4}", issued) or "Invalid" in issued:
        problems.append(f"#cert-issued-at is not a date: '{issued}'")
    if not page.locator("#cert-notes").is_visible():
        problems.append("#cert-notes is not visible")
    for selector, href in (
        ("#cert-piece-link", f"/piezas/{fx.piece_slug}/"),
        ("#cert-artisan-link", f"/artesanos/{fx.artisan_slug}/"),
    ):
        actual = page.get_attribute(selector, "href")
        if actual != href:
            problems.append(f"{selector} href is '{actual}', expected '{href}'")
    for value in page.eval_on_selector_all("[href], [src]", "els => els.map(e => e.getAttribute('href') || e.getAttribute('src'))"):
        if value and SECRETS.find(value):
            problems.append("a link/src attribute carries a secret")
    if set(body) != {"authenticity", "piece", "artisan", "authenticity_metadata"}:
        problems.append(f"response top-level keys are {sorted(body)}")
    if body.get("authenticity_metadata") != {"notes": fx.notes}:
        problems.append("authenticity_metadata is not exactly the allow-listed {'notes': ...}")
    if fx.canary in json.dumps(body) or fx.canary in page.content():
        problems.append("internal metadata canary leaked into the response or the DOM")
    return problems


def _follow_piece_link(page, fx: Fixtures, frontend_url: str) -> list[str]:
    problems: list[str] = []
    with page.expect_navigation(wait_until="load") as navigation:
        page.click("#cert-piece-link")
    response = navigation.value
    if response is None or response.status != 200:
        problems.append("public piece page did not load with 200")
        return problems
    if response.request.all_headers().get("referer"):
        problems.append("a Referer header was sent from /c/{token} to the public piece page")
    if page.evaluate("document.referrer") != "":
        problems.append("document.referrer is not empty on the public piece page")
    if page.url != f"{frontend_url}/piezas/{fx.piece_slug}/":
        problems.append(f"public piece page URL is {scrub(page.url)}")
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    html = page.content()
    problems += [f"public piece page DOM contains {p}" for p in SECRETS.find(html)]
    problems += [
        f"public piece page DOM contains {label}"
        for label, value in fx.all_secret_strings()
        if value in html and not label.startswith(("token:", "token_hash:"))
    ]
    return problems


def run_case(browser, viewport: dict, case: Case, fx: Fixtures, frontend_url: str, api_origin: str):
    """-> (problems, convergence signature | None)"""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    problems: list[str] = []
    signature = None
    token = case.token(fx) if case.token else None
    url = frontend_url + case.path(fx)
    third_party: list[str] = []

    context = browser.new_context(viewport=viewport, locale="es-MX", service_workers="block")
    try:

        def on_third_party(route) -> None:  # noqa: ANN001
            host = urlsplit(route.request.url).hostname or ""
            if host in FONT_HOSTS:
                route.fulfill(status=200, body="", content_type="text/css")
            else:
                third_party.append(host or "(no host)")
                route.abort()

        context.route(lambda u: (urlsplit(u).hostname or "") not in LOOPBACK, on_third_party)
        requests: list = []
        console: list[tuple[str, str]] = []
        page_errors: list[str] = []
        context.on("request", lambda r: requests.append(r))
        page = context.new_page()
        page.on("console", lambda m: console.append((m.type, m.text)))
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        if case.stub == "abort":
            page.route(api_origin + RESOLVE_PATH, lambda route: route.abort("failed"))
        elif case.stub == "http500":
            page.route(
                api_origin + RESOLVE_PATH,
                lambda route: route.fulfill(status=500, content_type="application/json", body='{"error":{"code":"internal_error"}}'),
            )

        try:
            response = page.goto(url, wait_until="load")
        except PlaywrightError as exc:
            return [f"navigation failed: {scrub(str(exc).splitlines()[0])}"], None

        try:
            page.wait_for_function(FINAL_STATE_JS, timeout=8000)
        except PlaywrightTimeout:
            problems.append("no final state (unavailable/error/authentic) became visible within 8s")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeout:
            pass

        # 1. navigation ---------------------------------------------------------
        if response is None or response.status != 200:
            problems.append(f"document status is {response.status if response else None}, expected 200")
        else:
            if response.request.redirected_from is not None:
                problems.append("the document navigation was redirected (the rewrite must be a 200, not a 3xx)")
            headers = response.all_headers()
            if "no-store" not in headers.get("cache-control", "").lower():
                problems.append("document Cache-Control lacks no-store")
            if "noindex" not in headers.get("x-robots-tag", "").lower():
                problems.append("document X-Robots-Tag lacks noindex")
        if page.url != url:
            problems.append(f"final URL differs from the requested one: {scrub(urlsplit(page.url).path)} (expected {scrub(urlsplit(url).path)})")
        if page.title() != SHELL_TITLE:
            problems.append(f"page title is '{page.title()}', expected the certificate shell (Home served?)")
        if page.locator("#hero").count() != 0:
            problems.append("the Home #hero section is present")

        # 2. requests -----------------------------------------------------------
        main_doc = next((r for r in requests if r.resource_type == "document"), None)
        api_requests = [r for r in requests if r.url == api_origin or r.url.startswith(api_origin + "/")]
        resolve = [r for r in requests if urlsplit(r.url).path.endswith("/certificates/resolve")]
        resolve_req = resolve[0] if resolve else None

        def allowed(r) -> bool:
            if r in resolve or r.method == "OPTIONS":
                return True
            # The authentic page loads the piece image from the API origin
            # (API_CONTRACT.md section 6.1). Only GET /media/* and only there.
            return case.state == "authentic" and r.method == "GET" and urlsplit(r.url).path.startswith("/media/")

        strays = [r for r in api_requests if not allowed(r)]
        if strays:
            problems.append(f"unexpected API requests: {[f'{r.method} {scrub(r.url)}' for r in strays]}")

        if token is None:
            if resolve or api_requests:
                problems.append(f"expected ZERO API/resolve requests, saw {len(api_requests)} API and {len(resolve)} resolve")
        elif len(resolve) != 1:
            problems.append(f"expected exactly 1 resolve request, saw {len(resolve)}")
        else:
            r = resolve[0]
            if r.method != "POST":
                problems.append(f"resolve method is {r.method}, expected POST")
            if r.url != api_origin + RESOLVE_PATH:
                problems.append(f"resolve URL is not exactly {RESOLVE_PATH} with an empty query: {scrub(r.url)}")
            if "application/json" not in r.all_headers().get("content-type", ""):
                problems.append("resolve Content-Type is not application/json")
            try:
                body = json.loads(r.post_data or "")
            except ValueError:
                problems.append("resolve body is not JSON")
            else:
                if not isinstance(body, dict) or list(body) != ["token"] or body["token"] != token:
                    problems.append("resolve JSON body is not exactly {'token': <expected token>}")

            # 3. response -------------------------------------------------------
            resolved = r.response()
            if case.stub == "abort":
                if r.failure is None:
                    problems.append("stubbed resolve request did not fail")
            elif case.stub == "http500":
                if resolved is None or resolved.status != 500:
                    problems.append("stubbed HTTP 500 was not delivered")
            elif resolved is None:
                problems.append("resolve request got no response")
            else:
                response_headers = resolved.all_headers()
                if "no-store" not in response_headers.get("cache-control", "").lower():
                    problems.append("resolve response lacks Cache-Control: no-store")
                if resolved.status != 200:
                    # Never read the body of a non-200 here: the page does not consume it,
                    # so Playwright would wait for it forever and hang the run.
                    problems.append(f"resolve status is {resolved.status}, expected 200")
                else:
                    raw = resolved.body()
                    if SECRETS.find(raw) or SECRETS.find(_flat(response_headers)):
                        problems.append("resolve response echoes a token or hash")
                    try:
                        payload = json.loads(raw)
                        status = payload["authenticity"]["status"]
                    except (ValueError, KeyError, TypeError):
                        problems.append("resolve response is not the documented JSON shape")
                        payload, status = {}, None
                    if status != case.state:
                        problems.append(f"resolve authenticity.status is {status!r}, expected {case.state!r}")
                    if case.state == "unavailable" and list(payload) != ["authenticity"]:
                        problems.append(f"unavailable body has extra keys: {sorted(payload)}")
                    if case.converges:
                        signature = (resolved.status, response_headers.get("content-type"), raw)

        # 4. UI + privacy ---------------------------------------------------------
        visible = [i for i in ALL_STATE_IDS if page.locator("#" + i).is_visible()]
        if visible != [STATE_ID[case.state]]:
            problems.append(f"visible states are {visible}, expected only ['{STATE_ID[case.state]}']")
        elif case.state in HEADING:
            heading = (page.locator(f"#{STATE_ID[case.state]} .certificate-heading").text_content() or "").strip()
            if heading != HEADING[case.state]:
                problems.append(f"heading is '{heading}', expected '{HEADING[case.state]}'")
            for selector in ("#cert-piece-title", "#cert-artisan-name", "#cert-notes"):
                if (page.locator(selector).text_content() or "").strip():
                    problems.append(f"{selector} holds data although the state is {case.state}")
            if case.converges:
                signature = (signature, (page.locator("#cert-unavailable").inner_text() or "").strip())
        elif case.state == "authentic" and resolve_req is not None and case.stub is None:
            try:
                problems += _authentic_problems(page, fx, json.loads(resolve_req.response().body()))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"authentic content check failed: {scrub(type(exc).__name__)}")

        html = page.content()
        problems += [f"rendered HTML contains {p}" for p in SECRETS.find(html)]
        browser_state = page.evaluate(BROWSER_STATE_JS)
        expected_state = {"local": 0, "session": 0, "cookie": "", "name": "", "history": "null", "referrer": "", "idb": 0, "caches": 0, "sw": 0}
        for key, want in expected_state.items():
            if browser_state.get(key) != want:
                problems.append(f"browser {key} is {scrub(repr(browser_state.get(key)))}, expected {want!r}")
        problems += [f"browser state contains {p}" for p in SECRETS.find(json.dumps(browser_state))]

        console_text = "\n".join(t for _, t in console) + "\n".join(page_errors)
        problems += [f"console/page errors contain {p}" for p in SECRETS.find(console_text)]
        if page_errors:
            problems.append(f"uncaught page errors: {len(page_errors)}")
        noisy = [c for c in console if c[0] in ("error", "warning")]
        if noisy and case.stub is None:
            problems.append(f"unexpected console output: {[(t, scrub(m)[:80]) for t, m in noisy]}")
        if third_party:
            problems.append(f"requests left loopback: {sorted(set(third_party))}")

        if case.name == "valid token" and case.stub is None and not problems:
            problems += _follow_piece_link(page, fx, frontend_url)

        problems += _leak_problems(requests, main_doc, resolve_req)
    finally:
        context.close()
    return problems, signature


def run(report: Report, fx: Fixtures, frontend_url: str, api_origin: str, tmp_dir: str) -> None:
    from playwright.sync_api import sync_playwright

    report.section("Browser: /c/{token} in Chromium (each case at desktop 1280x800 and mobile 390x844)")
    signatures: dict[str, list[tuple[str, object]]] = {name: [] for name in VIEWPORTS}
    profile_problems: list[str] = []
    profile_dirs: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            report.note(f"Chromium {browser.version}, no trace/video/screenshot/HAR, non-persistent contexts, third parties stubbed")
            for viewport_name, viewport in VIEWPORTS.items():
                for case in CASES:
                    with _Watchdog(f"[{viewport_name}] {case.name}"):
                        problems, signature = run_case(browser, viewport, case, fx, frontend_url, api_origin)
                    posts = "0 API requests" if case.token is None else "1 POST" if case.stub is None else f"1 POST ({case.stub})"
                    report.group(f"[{viewport_name}] {case.name}: {case.state}, {posts}", problems)
                    if signature is not None:
                        signatures[viewport_name].append((case.name, signature))
            profile_dirs = glob.glob(os.path.join(tmp_dir, "playwright_chromiumdev_profile-*"))
            for directory in profile_dirs:
                _, hits = scan_tree(directory)
                profile_problems += hits
        finally:
            browser.close()

    for viewport_name, items in signatures.items():
        distinct = {repr(sig) for _, sig in items}
        report.check(
            len(items) == 4 and len(distinct) == 1,
            f"[{viewport_name}] invalid / revoked / unpublished / wrong-length converge on ONE identical 'unavailable' response and page",
            f"{len(items)} responses, {len(distinct)} distinct",
        )
    report.group("Chromium profile on disk held no token or hash while the browser was open", profile_problems)
    leftover = glob.glob(os.path.join(tmp_dir, "playwright_chromiumdev_profile-*"))
    report.check(not leftover, "no Chromium profile directory persists after the browser closes")
