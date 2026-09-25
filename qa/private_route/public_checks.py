"""Chromium checks for the public entity pages (F-08, Playwright, headless).

The public API is the only authority on publication. These checks prove that a
public page shows entity content ONLY after a valid 200, and that every other
outcome leaves nothing of any entity on the page:

  * the served HTML of the shells and lists holds no entity content at all;
  * ok           the entity comes from the API payload and nothing else;
  * not_found    404 (unknown, draft, archived, a piece under an unpublished
                 artisan) renders ONE identical "not available" page, noindex;
  * unavailable  5xx / 429 / abort / timeout / malformed / host without an API
                 base render a different, retryable page, noindex;
  * loading      no entity content while the response is pending;
  * no JS        a neutral notice, no API request, no entity content;
  * lists        exactly what the API returns; `data: []` is an empty state.

Each case runs at desktop 1280x800 and mobile 390x844 against the real API
(disposable database, real seed + the F-08 visibility fixtures) served through
the frontend server under test. No trace, video, screenshot or HAR is written;
contexts are non-persistent and every third-party host is stubbed or blocked.
"""
from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Iterator
from urllib.parse import urlsplit

from private_route import fixtures as fxm
from private_route.browser_checks import FONT_HOSTS, LOOPBACK, VIEWPORTS, _Watchdog
from private_route.common import Report, scrub
from private_route.fixtures import Fixtures
from private_route.isolation_checks import _get

UNRESOLVED_HOST = "qa-unresolved.test"  # not in api-config.js's allowlist -> no API base
PLACEHOLDER = "/assets/img/card-placeholder.jpg"
FINAL_WAIT_MS = 15000  # the API client itself times out at 5s

DETAIL_IDS = ("entity-nojs", "entity-loading", "entity-not-found", "entity-unavailable", "entity-content")
LIST_IDS = ("list-nojs", "list-loading", "list-empty", "list-unavailable")

TEXT = {
    "piece": {
        "not-found-heading": "Pieza no disponible",
        "not-found": "Esta pieza no está disponible.",
        "unavailable-heading": "No pudimos cargar la pieza",
        "title": "Pieza — ArtesaNFC",
        "prefix": "/piezas/",
        "api": "/api/v1/pieces/",
    },
    "artisan": {
        "not-found-heading": "Artesano no disponible",
        "not-found": "Este perfil no está disponible.",
        "unavailable-heading": "No pudimos cargar el perfil",
        "title": "Artesano — ArtesaNFC",
        "prefix": "/artesanos/",
        "api": "/api/v1/artisans/",
    },
}

MALFORMED_STUBS = {
    "HTTP 500": ("status", 500, '{"error":{"code":"internal_error"}}', "application/json"),
    "HTTP 429": ("status", 429, '{"error":{"code":"rate_limited"}}', "application/json"),
    "request aborted": ("abort",),
    "200 with the wrong JSON shape": ("status", 200, '{"unexpected": true}', "application/json"),
    "200 with a non-JSON body": ("status", 200, "<html>gateway</html>", "text/html"),
    "response never arrives (5s client timeout)": ("hang",),
}


def entity_strings() -> list[str]:
    """Every name that must never come from anywhere but the API: the seed's
    published demo entities, and every non-public fixture."""
    from app.db.seed import ARTISANS, PIECES

    names = [a["full_name"] for a in ARTISANS] + [p["name"] for p in PIECES]
    names += [name for _, _, name in fxm.HIDDEN_ENTITIES]
    names.append(fxm.ARTISAN_WITHOUT_PUBLISHED_PIECES_NAME)
    return names


def hidden_names() -> list[str]:
    return [name for _, _, name in fxm.HIDDEN_ENTITIES]


@dataclass
class Visit:
    page: object
    url: str
    response: object
    requests: list = field(default_factory=list)
    responses: list = field(default_factory=list)
    console: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    third_party: list = field(default_factory=list)
    held: list = field(default_factory=list)

    def api_requests(self, api_origin: str) -> list[str]:
        """'METHOD /path' of every request to the API origin except media."""
        out = []
        for r in self.requests:
            parts = urlsplit(r.url)
            if f"{parts.scheme}://{parts.netloc}" == api_origin and not parts.path.startswith("/media/"):
                out.append(f"{r.method} {parts.path}")
        return out

    def release(self) -> None:
        for route in self.held:
            route.continue_()
        self.held.clear()

    def visible(self, ids) -> list[str]:
        return [i for i in ids if self.page.locator("#" + i).is_visible()]

    def text_of(self, selector: str) -> str:
        return (self.page.locator(selector).first.text_content() or "").strip()


def _install_stub(visit: Visit, api_origin: str, stub: tuple | None) -> None:
    if stub is None:
        return
    state = {"calls": 0}

    def handler(route) -> None:  # noqa: ANN001
        kind = stub[0]
        if kind == "abort":
            route.abort("failed")
        elif kind == "status":
            route.fulfill(status=stub[1], content_type=stub[3], body=stub[2])
        elif kind == "hold":
            visit.held.append(route)  # answered later, by Visit.release()
        elif kind == "hang":
            pass  # never answered: the client's own 5s timeout must fire
        elif kind == "flaky":  # first call fails with 500, later calls reach the real API
            state["calls"] += 1
            if state["calls"] == 1:
                route.fulfill(status=500, content_type="application/json", body="{}")
            else:
                route.continue_()

    visit_page = visit.page
    visit_page.route(lambda url: url.startswith(api_origin + "/api/v1/"), handler)


@contextlib.contextmanager
def open_page(
    browser, viewport, url: str, api_origin: str, *, js: bool = True, stub: tuple | None = None, wait_ids=None,
    allow_host: str | None = None,
) -> Iterator[Visit]:
    from playwright.sync_api import Error as PlaywrightError

    context = browser.new_context(viewport=viewport, locale="es-MX", service_workers="block", java_script_enabled=js)
    try:
        third_party: list[str] = []

        def on_other_host(route) -> None:  # noqa: ANN001
            host = urlsplit(route.request.url).hostname or ""
            if host in FONT_HOSTS:
                route.fulfill(status=200, body="", content_type="text/css")
            elif allow_host and host == allow_host:
                route.continue_()
            else:
                third_party.append(host or "(no host)")
                route.abort()

        context.route(lambda u: (urlsplit(u).hostname or "") not in LOOPBACK, on_other_host)
        page = context.new_page()
        visit = Visit(page=page, url=url, response=None, third_party=third_party)
        context.on("request", lambda r: visit.requests.append(r))
        context.on("response", lambda r: visit.responses.append(r))
        page.on("console", lambda m: visit.console.append((m.type, m.text)))
        page.on("pageerror", lambda e: visit.errors.append(str(e)))
        _install_stub(visit, api_origin, stub)
        try:
            visit.response = page.goto(url, wait_until="load")
        except PlaywrightError as exc:
            visit.errors.append(f"navigation failed: {scrub(str(exc).splitlines()[0])}")
        if wait_ids and js and visit.response is not None:
            selector = ", ".join("#" + i for i in wait_ids)
            try:
                page.wait_for_function(
                    "(sel) => Array.from(document.querySelectorAll(sel)).some(e => e.getClientRects().length > 0)",
                    arg=selector,
                    timeout=FINAL_WAIT_MS,
                )
            except Exception:  # noqa: BLE001 - reported by the caller as a wrong visible state
                pass
        yield visit
    finally:
        context.close()


def _common_problems(v: Visit, api_origin: str, *, expect_api: list[str], allow_http_errors: tuple[int, ...] = ()) -> list[str]:
    problems: list[str] = []
    if v.response is None:
        return v.errors or ["no navigation response"]
    if v.response.status != 200:
        problems.append(f"document status {v.response.status}, expected 200 (the shell is a rewrite, not an error)")
    if v.response.request.redirected_from is not None:
        problems.append("the document navigation was redirected")
    if urlsplit(v.page.url).path != urlsplit(v.url).path:
        problems.append(f"final path {urlsplit(v.page.url).path} differs from requested {urlsplit(v.url).path}")
    actual = v.api_requests(api_origin)
    if actual != expect_api:
        problems.append(f"API requests were {actual}, expected {expect_api}")
    for r in v.responses:
        parts = urlsplit(r.url)
        is_media = parts.path.startswith("/media/")
        if r.status >= 400 and not is_media and r.status not in allow_http_errors:
            problems.append(f"unexpected HTTP {r.status} for {scrub(parts.path)}")
    if v.errors:
        problems.append(f"uncaught page errors: {len(v.errors)}")
    for kind, text in v.console:
        if kind in ("error", "warning") and not (text.startswith("[ArtesaNFC]") or "Failed to load resource" in text):
            problems.append(f"unexpected console output: {scrub(text)[:90]}")
    if v.third_party:
        problems.append(f"requests left loopback: {sorted(set(v.third_party))}")
    return problems


def _raw_html_problems(v: Visit) -> list[str]:
    """The HTML the server sent (before any script): no entity content."""
    raw = v.response.text() if v.response is not None else ""
    problems = [f"served HTML contains {name!r}" for name in entity_strings() if name in raw]
    if re.search(r"data-(piece|artisan)-slug", raw):
        problems.append("served HTML carries a per-entity slug attribute")
    return problems


def _robots(v: Visit) -> str | None:
    return v.page.evaluate("() => { const m = document.querySelector('meta[name=robots]'); return m ? m.content : null; }")


def _no_entity_dom(v: Visit) -> list[str]:
    html = v.page.content()
    return [f"the DOM contains {name!r}" for name in entity_strings() if name in html]


def _api_json(api_origin: str, path: str) -> dict:
    status, text = _get(api_origin, path)
    if status != 200:
        raise RuntimeError(f"QA setup: GET {path} returned {status}")
    return json.loads(text)


# --- detail pages ------------------------------------------------------------------------------------------------------


def _state_problems(v: Visit, kind: str, state: str) -> list[str]:
    problems: list[str] = []
    visible = v.visible(DETAIL_IDS)
    expected = [f"entity-{state}"]
    if visible != expected:
        problems.append(f"visible states are {visible}, expected {expected}")
        return problems
    text = TEXT[kind]
    if state == "not-found":
        if v.text_of("#entity-not-found h1") != text["not-found-heading"] or text["not-found"] not in v.text_of("#entity-not-found"):
            problems.append("the not-found page does not show the neutral not-available text")
        if _robots(v) != "noindex":
            problems.append(f"robots meta is {_robots(v)!r}, expected 'noindex'")
        if v.page.title() != text["title"]:
            problems.append(f"title changed to {v.page.title()!r}; it must stay the neutral shell title")
        problems += _no_entity_dom(v)
    elif state == "unavailable":
        if v.text_of("#entity-unavailable h1") != text["unavailable-heading"]:
            problems.append("the unavailable page does not show the retryable heading")
        if not v.page.locator("#entity-retry").is_visible():
            problems.append("no visible retry control")
        if _robots(v) != "noindex":
            problems.append(f"robots meta is {_robots(v)!r}, expected 'noindex'")
        problems += _no_entity_dom(v)
    elif state == "nojs":
        problems += _no_entity_dom(v)
    return problems


def _ok_piece_problems(v: Visit, fx: Fixtures, frontend_url: str) -> list[str]:
    from app.db.seed import PIECES

    seed = next(p for p in PIECES if p["slug"] == fx.piece_slug)
    problems: list[str] = []

    def expect(label: str, actual: str, wanted: str) -> None:
        if actual != wanted:
            problems.append(f"{label} is {actual!r}, expected {wanted!r}")

    expect("h1", v.text_of("#piece-title"), fx.piece_name)
    expect("meta line", v.text_of("#piece-meta"), seed["technique"])
    expect("description", v.text_of("#piece-description"), seed["description"])
    expect("document title", v.page.title(), f"{fx.piece_name} — ArtesaNFC")
    expect("canonical", v.page.get_attribute('link[rel="canonical"]', "href") or "", f"{frontend_url}/piezas/{fx.piece_slug}/")
    if _robots(v) is not None:
        problems.append(f"robots meta is {_robots(v)!r}; a valid entity must be indexable (no robots meta)")
    expect("artisan link href", v.page.get_attribute("#piece-artisan .editorial-link", "href") or "", f"/artesanos/{fx.artisan_slug}/")
    expect("artisan link text", v.text_of("#piece-artisan .editorial-link"), f"Creada por {fx.artisan_display_name} →")
    for section in ("piece-gallery", "piece-history", "piece-materials"):
        if v.page.locator("#" + section).is_visible():
            problems.append(f"#{section} is visible although the API sent no data for it")
    hero_alt = v.page.get_attribute("#piece-hero img", "alt")
    expect("hero alt", hero_alt or "", f"Fotografía ficticia de demostración de {fx.piece_name}")
    v.page.wait_for_load_state("networkidle")
    src = v.page.evaluate("() => document.querySelector('#piece-hero img').getAttribute('src')")
    if not src.endswith(PLACEHOLDER):
        problems.append(f"the hero did not fall back to the placeholder (media is not served): src={scrub(src)}")
    body = v.page.content()
    problems += [f"the DOM contains the hidden entity {name!r}" for name in hidden_names() if name in body]
    return problems


def _ok_artisan_problems(v: Visit, fx: Fixtures, api_origin: str, frontend_url: str) -> list[str]:
    from app.db.seed import ARTISANS

    seed = next(a for a in ARTISANS if a["slug"] == fx.artisan_slug)
    api = _api_json(api_origin, f"/api/v1/artisans/{fx.artisan_slug}")
    problems: list[str] = []

    def expect(label: str, actual: str, wanted: str) -> None:
        if actual != wanted:
            problems.append(f"{label} is {actual!r}, expected {wanted!r}")

    expect("h1", v.text_of("#artisan-title"), fx.artisan_display_name)
    expect("meta line", v.text_of("#artisan-meta"), f"{seed['locality']}, Oaxaca — {seed['techniques'][0]}")
    expect("biography", v.text_of("#artisan-biography"), seed["biography"])
    expect("document title", v.page.title(), f"{fx.artisan_display_name} — ArtesaNFC")
    expect("canonical", v.page.get_attribute('link[rel="canonical"]', "href") or "", f"{frontend_url}/artesanos/{fx.artisan_slug}/")
    if _robots(v) is not None:
        problems.append(f"robots meta is {_robots(v)!r}; a valid entity must be indexable")
    if v.page.locator("#artisan-history").is_visible():
        problems.append("#artisan-history is visible although the API sent no history")
    expect("techniques", v.text_of("#artisan-techniques li"), seed["techniques"][0])

    cards = v.page.eval_on_selector_all(
        "#artisan-pieces .card",
        "els => els.map(e => [e.querySelector('.card__title').textContent, e.querySelector('a').getAttribute('href')])",
    )
    wanted = [[p["name"], "/piezas/" + p["slug"] + "/"] for p in api["pieces"]]
    if cards != wanted or not wanted:
        problems.append(f"piece cards are {cards}, expected exactly the API's published pieces {wanted}")
    body = v.page.content()
    problems += [f"the DOM contains the hidden entity {name!r}" for name in hidden_names() if name in body]
    return problems


@dataclass(frozen=True)
class DetailCase:
    name: str
    kind: str  # piece | artisan
    slug: Callable[[Fixtures], str]
    state: str  # content | not-found | unavailable | nojs
    stub: tuple | None = None
    js: bool = True
    host: str | None = None
    zero_api: bool = False  # the page must not even ask the API
    path_override: Callable[[Fixtures], str] | None = None
    api_slug: Callable[[Fixtures], str] | None = None  # slug the API is expected to be asked for
    converges: bool = False  # part of the "every 404 is the same page" group


def _slug(value: str) -> Callable[[Fixtures], str]:
    return lambda fx: value


PIECE_404 = [
    ("unknown slug", _slug("qa-does-not-exist")),
    ("draft piece", _slug(fxm.UNPUBLISHED_PIECE_SLUG)),
    ("archived piece", _slug(fxm.ARCHIVED_PIECE_SLUG)),
    ("published piece under an unpublished artisan", _slug(fxm.PIECE_UNDER_UNPUBLISHED_ARTISAN_SLUG)),
]
ARTISAN_404 = [
    ("unknown slug", _slug("qa-does-not-exist")),
    ("draft artisan", _slug(fxm.UNPUBLISHED_ARTISAN_SLUG)),
    ("archived artisan", _slug(fxm.ARCHIVED_ARTISAN_SLUG)),
]


def detail_cases() -> list[DetailCase]:
    cases: list[DetailCase] = []
    cases.append(DetailCase("valid published piece", "piece", lambda fx: fx.piece_slug, "content"))
    cases.append(DetailCase("valid published artisan", "artisan", lambda fx: fx.artisan_slug, "content"))
    cases.append(
        DetailCase(
            "published artisan whose only piece is a draft (no piece is listed)", "artisan",
            _slug(fxm.ARTISAN_WITHOUT_PUBLISHED_PIECES_SLUG), "content",
        )
    )
    for label, slug in PIECE_404:
        cases.append(DetailCase(f"404 piece: {label}", "piece", slug, "not-found", converges=True))
    for label, slug in ARTISAN_404:
        cases.append(DetailCase(f"404 artisan: {label}", "artisan", slug, "not-found", converges=True))
    cases.append(DetailCase("404 piece: slug 'index.html' (/piezas/index.html is captured by the rewrite)", "piece", _slug("index.html"), "not-found"))
    for label, stub in MALFORMED_STUBS.items():
        cases.append(DetailCase(f"unavailable: piece, {label}", "piece", lambda fx: fx.piece_slug, "unavailable", stub=stub))
    cases.append(DetailCase("unavailable: artisan, HTTP 500", "artisan", lambda fx: fx.artisan_slug, "unavailable", stub=MALFORMED_STUBS["HTTP 500"]))
    cases.append(DetailCase("unavailable: artisan, request aborted", "artisan", lambda fx: fx.artisan_slug, "unavailable", stub=MALFORMED_STUBS["request aborted"]))
    cases.append(DetailCase("unavailable: piece, host without an API base", "piece", lambda fx: fx.piece_slug, "unavailable", host=UNRESOLVED_HOST, zero_api=True))
    cases.append(DetailCase("unavailable: artisan, host without an API base", "artisan", lambda fx: fx.artisan_slug, "unavailable", host=UNRESOLVED_HOST, zero_api=True))
    cases.append(DetailCase("no JavaScript: piece", "piece", lambda fx: fx.piece_slug, "nojs", js=False, zero_api=True))
    cases.append(DetailCase("no JavaScript: artisan", "artisan", lambda fx: fx.artisan_slug, "nojs", js=False, zero_api=True))
    for label, path in (
        ("encoded slash in the slug", "/piezas/qa%2Fslash/"),
        ("300-character slug", "/piezas/" + "a" * 300 + "/"),
        ("invalid percent-encoding", "/piezas/%E0%A4%A/"),
    ):
        cases.append(DetailCase(f"not found without asking the API: {label}", "piece", _slug("-"), "not-found", zero_api=True, path_override=(lambda p: lambda fx: p)(path)))
    cases.append(DetailCase("not found without asking the API: shell opened directly (/_shell/pieza/)", "piece", _slug("-"), "not-found", zero_api=True, path_override=lambda fx: "/_shell/pieza/"))
    cases.append(DetailCase("not found without asking the API: artisan shell opened directly", "artisan", _slug("-"), "not-found", zero_api=True, path_override=lambda fx: "/_shell/artesano/"))
    return cases


def run_detail_case(browser, viewport, case: DetailCase, fx: Fixtures, frontend_url: str, api_origin: str):
    """-> (problems, convergence signature | None)"""
    text = TEXT[case.kind]
    slug = case.slug(fx)
    path = case.path_override(fx) if case.path_override else f"{text['prefix']}{slug}/"
    base = frontend_url
    if case.host:
        base = f"http://{case.host}:{urlsplit(frontend_url).port}"
    url = base + path
    expect_api = [] if case.zero_api else [f"GET {text['api']}{(case.api_slug or case.slug)(fx)}"]
    wait_ids = {"content": ("entity-content",), "not-found": ("entity-not-found",), "unavailable": ("entity-unavailable",), "nojs": None}[case.state]

    with open_page(browser, viewport, url, api_origin, js=case.js, stub=case.stub, wait_ids=wait_ids, allow_host=case.host) as v:
        allowed = (404,) if case.state == "not-found" and not case.zero_api else ()
        if case.stub and case.stub[0] == "status":
            allowed += (case.stub[1],)
        problems = _common_problems(v, api_origin, expect_api=expect_api, allow_http_errors=allowed)
        if v.response is None:
            return problems, None
        problems += _raw_html_problems(v)
        problems += _state_problems(v, case.kind, "content" if case.state == "content" else case.state) if case.state != "content" else []
        if case.state == "content":
            visible = v.visible(DETAIL_IDS)
            if visible != ["entity-content"]:
                problems.append(f"visible states are {visible}, expected ['entity-content']")
            elif case.kind == "piece":
                problems += _ok_piece_problems(v, fx, frontend_url)
            elif slug == fx.artisan_slug:
                problems += _ok_artisan_problems(v, fx, api_origin, frontend_url)
            else:  # artisan without any published piece
                if v.text_of("#artisan-title") != fxm.ARTISAN_WITHOUT_PUBLISHED_PIECES_NAME:
                    problems.append(f"h1 is {v.text_of('#artisan-title')!r}")
                if v.page.locator("#artisan-pieces").is_visible() or v.page.locator("#artisan-pieces .card").count():
                    problems.append("the pieces section is visible although the artisan has no published piece")
                body = v.page.content()
                problems += [f"the DOM contains the hidden entity {n!r}" for n in hidden_names() if n in body]
        signature = None
        if case.converges and case.state == "not-found":
            signature = (v.text_of("#entity-not-found"), v.page.title(), _robots(v))
    return problems, signature


def run_loading_case(browser, viewport, fx: Fixtures, frontend_url: str, api_origin: str) -> list[str]:
    """While the API has not answered, the page holds no entity content; only
    once it does is the entity shown."""
    url = f"{frontend_url}/piezas/{fx.piece_slug}/"
    with open_page(browser, viewport, url, api_origin, stub=("hold",), wait_ids=("entity-loading",)) as v:
        problems: list[str] = []
        if v.visible(DETAIL_IDS) != ["entity-loading"]:
            problems.append(f"while pending, visible states are {v.visible(DETAIL_IDS)}, expected ['entity-loading']")
        if not v.held:
            problems.append("the API request never reached the stub")
        problems += _no_entity_dom(v)
        if v.page.title() != TEXT["piece"]["title"]:
            problems.append(f"title while pending is {v.page.title()!r}")
        if v.page.get_attribute("#main-content", "aria-busy") != "true":
            problems.append("#main-content is not aria-busy while loading")
        v.release()
        try:
            v.page.wait_for_function("() => !document.getElementById('entity-content').hidden", timeout=FINAL_WAIT_MS)
        except Exception:  # noqa: BLE001
            problems.append("the entity never appeared after the API answered")
        else:
            if v.text_of("#piece-title") != fx.piece_name:
                problems.append("after the answer the title is not the API's")
            if v.page.get_attribute("#main-content", "aria-busy") != "false":
                problems.append("#main-content stays aria-busy after loading")
        return problems


def run_retry_case(browser, viewport, fx: Fixtures, frontend_url: str, api_origin: str) -> list[str]:
    """First request fails (500) -> unavailable + retry; the retry reaches the real API -> entity."""
    url = f"{frontend_url}/piezas/{fx.piece_slug}/"
    with open_page(browser, viewport, url, api_origin, stub=("flaky",), wait_ids=("entity-unavailable",)) as v:
        problems: list[str] = []
        if v.visible(DETAIL_IDS) != ["entity-unavailable"]:
            return [f"first attempt: visible states are {v.visible(DETAIL_IDS)}, expected ['entity-unavailable']"]
        problems += _no_entity_dom(v)
        v.page.click("#entity-retry")
        try:
            v.page.wait_for_function("() => !document.getElementById('entity-content').hidden", timeout=FINAL_WAIT_MS)
        except Exception:  # noqa: BLE001
            return problems + ["the retry did not produce the entity"]
        if v.text_of("#piece-title") != fx.piece_name:
            problems.append("after the retry the title is not the API's")
        if _robots(v) is not None:
            problems.append("noindex from the failed attempt was not cleared after a valid 200")
        actual = v.api_requests(api_origin)
        if actual != [f"GET /api/v1/pieces/{fx.piece_slug}"] * 2:
            problems.append(f"API requests were {actual}, expected exactly two (failed attempt + retry)")
        return problems


# --- lists --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ListCase:
    name: str
    page: str  # piezas | artesanos
    state: str  # list | empty | unavailable | nojs
    stub: tuple | None = None
    js: bool = True
    host: str | None = None
    zero_api: bool = False


EMPTY_LIST = ("status", 200, '{"data":[],"meta":{"total":0}}', "application/json")
LIST_API = {"piezas": "/api/v1/pieces", "artesanos": "/api/v1/artisans"}
LIST_HEADING = {"piezas": "Piezas", "artesanos": "Artesanos"}
LIST_EMPTY_TEXT = {"piezas": "Aún no hay piezas publicadas.", "artesanos": "Aún no hay artesanos publicados."}


def list_cases() -> list[ListCase]:
    cases: list[ListCase] = []
    for page in ("piezas", "artesanos"):
        cases.append(ListCase(f"/{page}/ with what the API publishes", page, "list"))
        cases.append(ListCase(f"/{page}/ when the API returns data: [] (empty state, nothing kept)", page, "empty", stub=EMPTY_LIST))
        cases.append(ListCase(f"/{page}/ when the API fails (HTTP 500)", page, "unavailable", stub=MALFORMED_STUBS["HTTP 500"]))
        cases.append(ListCase(f"/{page}/ when the API is rate limited (HTTP 429)", page, "unavailable", stub=MALFORMED_STUBS["HTTP 429"]))
        cases.append(ListCase(f"/{page}/ when the request is aborted", page, "unavailable", stub=MALFORMED_STUBS["request aborted"]))
        cases.append(ListCase(f"/{page}/ when the JSON has the wrong shape", page, "unavailable", stub=MALFORMED_STUBS["200 with the wrong JSON shape"]))
        cases.append(ListCase(f"/{page}/ when data is non-empty but holds no usable item", page, "unavailable", stub=("status", 200, '{"data":[{"x":1}],"meta":{"total":1}}', "application/json")))
        cases.append(ListCase(f"/{page}/ on a host without an API base", page, "unavailable", host=UNRESOLVED_HOST, zero_api=True))
        cases.append(ListCase(f"/{page}/ without JavaScript", page, "nojs", js=False, zero_api=True))
    return cases


def run_list_case(browser, viewport, case: ListCase, frontend_url: str, api_origin: str) -> list[str]:
    base = f"http://{case.host}:{urlsplit(frontend_url).port}" if case.host else frontend_url
    url = f"{base}/{case.page}/"
    ids = {"list": None, "empty": "list-empty", "unavailable": "list-unavailable", "nojs": "list-nojs"}[case.state]
    wait_ids = (ids,) if ids else None  # the list itself is awaited below
    expect_api = [] if case.zero_api else [f"GET {LIST_API[case.page]}"]
    with open_page(browser, viewport, url, api_origin, js=case.js, stub=case.stub, wait_ids=wait_ids, allow_host=case.host) as v:
        grid_wait_problem: list[str] = []
        if v.response is not None and case.state == "list":
            try:  # the list is the final state here; wait for it before looking at what was requested
                v.page.wait_for_function("(sel) => !document.querySelector(sel).hidden", arg=f"#{case.page} .card-grid", timeout=FINAL_WAIT_MS)
            except Exception:  # noqa: BLE001
                grid_wait_problem.append("the list never appeared")
        problems = grid_wait_problem + _common_problems(
            v, api_origin, expect_api=expect_api,
            allow_http_errors=(case.stub[1],) if case.stub and case.stub[0] == "status" else (),
        )
        if v.response is None:
            return problems
        problems += _raw_html_problems(v)
        section = f"#{case.page}"
        visible = v.visible(LIST_IDS)
        grid_visible = v.page.locator(f"{section} .card-grid").is_visible()
        cards = v.page.eval_on_selector_all(
            f"{section} .card",
            "els => els.map(e => [e.querySelector('.card__title').textContent, e.querySelector('a').getAttribute('href')])",
        )
        if case.state == "list":
            expected = _api_json(api_origin, LIST_API[case.page])["data"]
            prefix = "/piezas/" if case.page == "piezas" else "/artesanos/"
            wanted = [
                [(item["name"] if case.page == "piezas" else (item["artistic_name"] or item["full_name"])), prefix + item["slug"] + "/"]
                for item in expected
            ]
            if visible or not grid_visible:
                problems.append(f"expected only the list visible, saw states {visible} grid_visible={grid_visible}")
            if cards != wanted or not wanted:
                problems.append(f"cards are {cards}, expected exactly what the API publishes {wanted}")
            body = v.page.content()
            problems += [f"the list shows the hidden entity {n!r}" for n in hidden_names() if n in body]
        else:
            wanted_state = {"empty": ["list-empty"], "unavailable": ["list-unavailable"], "nojs": ["list-nojs"]}[case.state]
            if visible != wanted_state or grid_visible:
                problems.append(f"visible states are {visible} (grid visible: {grid_visible}), expected {wanted_state} and no grid")
            if cards:
                problems.append(f"{len(cards)} card(s) are on the page although the API did not confirm any entity")
            problems += _no_entity_dom(v)
            if case.state == "empty" and v.text_of("#list-empty") != LIST_EMPTY_TEXT[case.page]:
                problems.append(f"empty message is {v.text_of('#list-empty')!r}")
            if case.state == "unavailable" and not v.page.locator("#list-retry").is_visible():
                problems.append("no visible retry control")
        if v.page.locator("h1").first.text_content().strip() != LIST_HEADING[case.page]:
            problems.append("the page heading changed")
    return problems


def run_list_retry_case(browser, viewport, frontend_url: str, api_origin: str) -> list[str]:
    url = f"{frontend_url}/piezas/"
    with open_page(browser, viewport, url, api_origin, stub=("flaky",), wait_ids=("list-unavailable",)) as v:
        if v.visible(LIST_IDS) != ["list-unavailable"]:
            return [f"first attempt: visible states are {v.visible(LIST_IDS)}"]
        v.page.click("#list-retry")
        try:
            v.page.wait_for_function("() => !document.querySelector('#piezas .card-grid').hidden", timeout=FINAL_WAIT_MS)
        except Exception:  # noqa: BLE001
            return ["the retry did not produce the list"]
        count = v.page.locator("#piezas .card").count()
        api_count = len(_api_json(api_origin, LIST_API["piezas"])["data"])
        return [] if count == api_count else [f"after the retry there are {count} cards, the API has {api_count}"]


# --- runner --------------------------------------------------------------------------------------------------------------


def run(report: Report, fx: Fixtures, frontend_url: str, api_origin: str) -> None:
    from playwright.sync_api import sync_playwright

    report.section("Browser: public entity pages and lists (F-08; each case at desktop 1280x800 and mobile 390x844)")
    signatures: dict[str, list[tuple[str, object]]] = {name: [] for name in VIEWPORTS}
    cases = detail_cases()
    lists = list_cases()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=[f"--host-resolver-rules=MAP {UNRESOLVED_HOST} 127.0.0.1"])
        try:
            report.note(f"Chromium {browser.version}; {len(cases)} detail + {len(lists)} list cases + loading/retry per viewport")
            for viewport_name, viewport in VIEWPORTS.items():
                for case in cases:
                    with _Watchdog(f"[{viewport_name}] {case.name}"):
                        problems, signature = run_detail_case(browser, viewport, case, fx, frontend_url, api_origin)
                    report.group(f"[{viewport_name}] {case.name}: {case.state}", problems)
                    if signature is not None:
                        signatures[viewport_name].append((case.name, signature))
                with _Watchdog(f"[{viewport_name}] loading"):
                    report.group(f"[{viewport_name}] pending API answer: loading state, no entity content until the 200 arrives", run_loading_case(browser, viewport, fx, frontend_url, api_origin))
                with _Watchdog(f"[{viewport_name}] retry"):
                    report.group(f"[{viewport_name}] retry after a failed load: unavailable, then the entity", run_retry_case(browser, viewport, fx, frontend_url, api_origin))
                for case in lists:
                    with _Watchdog(f"[{viewport_name}] {case.name}"):
                        problems = run_list_case(browser, viewport, case, frontend_url, api_origin)
                    report.group(f"[{viewport_name}] {case.name}: {case.state}", problems)
                with _Watchdog(f"[{viewport_name}] list retry"):
                    report.group(f"[{viewport_name}] /piezas/ retry after a failed load", run_list_retry_case(browser, viewport, frontend_url, api_origin))
        finally:
            browser.close()

    for viewport_name, items in signatures.items():
        pieces = [s for n, s in items if n in {f"404 piece: {label}" for label, _ in PIECE_404}]
        artisans = [s for n, s in items if n in {f"404 artisan: {label}" for label, _ in ARTISAN_404}]
        report.check(
            len(pieces) == len(PIECE_404) and len({repr(s) for s in pieces}) == 1,
            f"[{viewport_name}] unknown / draft / archived / under-unpublished-artisan pieces converge on ONE identical not-found page",
            f"{len(pieces)} pages, {len({repr(s) for s in pieces})} distinct",
        )
        report.check(
            len(artisans) == len(ARTISAN_404) and len({repr(s) for s in artisans}) == 1,
            f"[{viewport_name}] unknown / draft / archived artisans converge on ONE identical not-found page",
            f"{len(artisans)} pages, {len({repr(s) for s in artisans}) or 0} distinct",
        )
