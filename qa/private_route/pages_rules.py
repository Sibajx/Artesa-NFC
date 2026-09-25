"""Parser and linter for the subset of Cloudflare Pages `_redirects` /
`_headers` semantics this QA depends on.

This is deliberately NOT a Pages emulator. It understands only:
  _redirects  `source destination [status]`, comments, status 200 (rewrite) and
              3xx (redirect). In a SOURCE: `*` is a splat (matches anything,
              the empty string included) and `:name` is a placeholder (exactly
              one non-empty path segment, `[^/]+`). Placeholders in a
              DESTINATION (substitution) are refused: nothing here uses them.
  _headers    URL pattern lines followed by indented `Name: value` lines. When
              several blocks match a path, values of the same header are joined
              with ", " (Pages behaviour observed in docs/SPRINT_4.md section 9).

The linter pins the production private-route rule and rejects the form that
broke the route before (docs/SPRINT_4.md section 12, blocker B1). It also pins
the public entity-shell rules (F-08): see PUBLIC_SHELL_RULES.

Precedence, observed on Wrangler 4.135.0 (docs/QA_PRIVATE_ROUTE.md section 5):
the FIRST matching rule wins and a 200 rewrite applies EVEN WHEN a static asset
exists at the requested path. `/piezas/*  /x/  200` therefore also captures
`/piezas/` (empty splat) and every existing page under /piezas/. That is why
the public rules use the single-segment placeholder `:slug`, which never
matches `/piezas/` and never crosses a `/`. A rule without a trailing slash
does not match the path with one, and vice versa: both forms are needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PRIVATE_SOURCE = "/c/*"
PRIVATE_DESTINATION = "/c/"
PRIVATE_STATUS = 200
PROBE_PATH = "/c/qa-probe"
REDIRECT_STATUSES = {200, 301, 302, 303, 307, 308}

# F-08: public detail routes are served by ONE neutral shell per entity type,
# living outside the /piezas/ and /artesanos/ namespaces. Exactly these rules.
PUBLIC_SHELL_RULES = (
    ("/piezas/:slug", "/_shell/pieza/", 200),
    ("/piezas/:slug/", "/_shell/pieza/", 200),
    ("/artesanos/:slug", "/_shell/artesano/", 200),
    ("/artesanos/:slug/", "/_shell/artesano/", 200),
)
LIST_PATHS = ("/piezas", "/piezas/", "/artesanos", "/artesanos/")
NESTED_PATHS = ("/piezas/qa-a/qa-b", "/piezas/qa-a/qa-b/", "/artesanos/qa-a/qa-b", "/artesanos/qa-a/qa-b/")
SLUG_PROBE = "qa-probe-slug"


class RulesError(ValueError):
    """A `_redirects` / `_headers` file this QA cannot interpret."""


@dataclass(frozen=True)
class Redirect:
    source: str
    destination: str
    status: int
    line: int

    def matches(self, path: str) -> bool:
        return _regex(self.source).match(path) is not None


@dataclass(frozen=True)
class HeaderBlock:
    pattern: str
    headers: tuple[tuple[str, str], ...]
    line: int

    def matches(self, path: str) -> bool:
        return _regex(self.pattern).match(path) is not None


_PATTERN_TOKEN = re.compile(r"\*|(?<=/):[A-Za-z][A-Za-z0-9_]*")


@lru_cache(maxsize=None)
def _regex(pattern: str) -> re.Pattern[str]:
    """`*` -> `.*` (splat, may be empty); `/:name` -> one non-empty segment."""
    parts: list[str] = []
    last = 0
    for token in _PATTERN_TOKEN.finditer(pattern):
        parts.append(re.escape(pattern[last : token.start()]))
        parts.append(".*" if token.group() == "*" else "[^/]+")
        last = token.end()
    parts.append(re.escape(pattern[last:]))
    return re.compile("^" + "".join(parts) + "$")


def parse_redirects(text: str) -> list[Redirect]:
    rules: list[Redirect] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) not in (2, 3):
            raise RulesError(f"_redirects line {number}: expected 'source destination [status]'")
        source, destination = parts[0], parts[1]
        try:
            status = int(parts[2]) if len(parts) == 3 else 302
        except ValueError:
            raise RulesError(f"_redirects line {number}: status is not a number") from None
        if status not in REDIRECT_STATUSES:
            raise RulesError(f"_redirects line {number}: unsupported status {status}")
        for value in (source, destination):
            if not (value.startswith("/") or value.startswith("https://") or value.startswith("http://")):
                raise RulesError(f"_redirects line {number}: '{value}' must start with '/' or a scheme")
        if re.search(r"/:[A-Za-z]", destination):
            raise RulesError(f"_redirects line {number}: placeholder substitution in a destination is not supported by this QA subset")
        rules.append(Redirect(source, destination, status, number))
    return rules


def parse_headers(text: str) -> list[HeaderBlock]:
    blocks: list[HeaderBlock] = []
    pattern: str | None = None
    pattern_line = 0
    headers: list[tuple[str, str]] = []

    def flush() -> None:
        if pattern is not None:
            blocks.append(HeaderBlock(pattern, tuple(headers), pattern_line))

    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0] not in " \t":
            flush()
            pattern, pattern_line, headers = raw.strip(), number, []
            if not (pattern.startswith("/") or pattern.startswith("https://")):
                raise RulesError(f"_headers line {number}: URL pattern must start with '/'")
            continue
        if pattern is None:
            raise RulesError(f"_headers line {number}: header before any URL pattern")
        name, sep, value = raw.strip().partition(":")
        if not sep or not name.strip() or name.strip().startswith("!"):
            raise RulesError(f"_headers line {number}: expected 'Name: value' (detach '!' is unsupported)")
        headers.append((name.strip(), value.strip()))
    flush()
    return blocks


def is_pages_loop_rule(rule: Redirect) -> bool:
    """Pages drops any splat rule whose destination ends in /index or
    /index.html as an 'infinite loop' (wrangler 4.135: 'Infinite loop detected
    in this rule and has been ignored'). This is the Sprint 4 blocker B1."""
    destination = rule.destination.split("?", 1)[0]
    return "*" in rule.source and re.search(r"/index(\.html)?$", destination) is not None


def effective_redirects(rules: list[Redirect]) -> tuple[list[Redirect], list[Redirect]]:
    kept = [r for r in rules if not is_pages_loop_rule(r)]
    dropped = [r for r in rules if is_pages_loop_rule(r)]
    return kept, dropped


def headers_for(path: str, blocks: list[HeaderBlock]) -> dict[str, str]:
    """Header name (lower case) -> value, joining repeats with ', '."""
    collected: dict[str, list[str]] = {}
    for block in blocks:
        if block.matches(path):
            for name, value in block.headers:
                collected.setdefault(name.lower(), []).append(value)
    return {name: ", ".join(values) for name, values in collected.items()}


def lint_redirects(rules: list[Redirect]) -> list[str]:
    problems: list[str] = []
    private = [r for r in rules if r.source == PRIVATE_SOURCE]
    if len(private) != 1:
        problems.append(
            f"expected exactly one '{PRIVATE_SOURCE}' rule, found {len(private)}"
            f" (required: '{PRIVATE_SOURCE}  {PRIVATE_DESTINATION}  {PRIVATE_STATUS}')"
        )
    else:
        rule = private[0]
        if (rule.destination, rule.status) != (PRIVATE_DESTINATION, PRIVATE_STATUS):
            problems.append(
                f"line {rule.line}: private route rule is '{rule.source}  {rule.destination}  {rule.status}',"
                f" required '{PRIVATE_SOURCE}  {PRIVATE_DESTINATION}  {PRIVATE_STATUS}'"
            )
    for rule in rules:
        if is_pages_loop_rule(rule):
            problems.append(
                f"line {rule.line}: '{rule.source}  {rule.destination}  {rule.status}' is silently ignored by"
                " Cloudflare Pages (splat rule to /index[.html]); /c/{token} would serve Home"
                " (docs/SPRINT_4.md section 12, blocker B1)"
            )
    effective, _ = effective_redirects(rules)
    first = next((r for r in effective if r.matches(PROBE_PATH)), None)
    if first is None or first.source != PRIVATE_SOURCE:
        problems.append(f"'{PROBE_PATH}' is not handled by the private route rule first")
    return problems


def _first_match(rules: list[Redirect], path: str) -> Redirect | None:
    return next((r for r in rules if r.matches(path)), None)


def lint_public_redirects(rules: list[Redirect]) -> list[str]:
    """F-08: the public entity routes are exactly PUBLIC_SHELL_RULES, they never
    shadow the list pages, and nothing captures a nested path."""
    problems: list[str] = []
    effective, _ = effective_redirects(rules)
    for source, destination, status in PUBLIC_SHELL_RULES:
        found = [r for r in rules if r.source == source]
        if len(found) != 1:
            problems.append(f"expected exactly one '{source}  {destination}  {status}' rule, found {len(found)}")
        elif (found[0].destination, found[0].status) != (destination, status):
            problems.append(
                f"line {found[0].line}: '{source}' is '{found[0].destination}  {found[0].status}',"
                f" required '{destination}  {status}'"
            )
    for path in LIST_PATHS:
        hit = _first_match(effective, path)
        if hit is not None and hit.status == 200:
            problems.append(
                f"line {hit.line}: '{hit.source}' rewrites the list route {path}; Pages applies a 200 rewrite even over an"
                " existing page, so the list would be replaced by the shell (a splat also matches the empty string)"
            )
    for entity, shell in (("piezas", "/_shell/pieza/"), ("artesanos", "/_shell/artesano/")):
        for suffix in ("", "/"):
            path = f"/{entity}/{SLUG_PROBE}{suffix}"
            hit = _first_match(effective, path)
            if hit is None or (hit.destination, hit.status) != (shell, 200):
                problems.append(f"{path} is not first handled by the rewrite to {shell}")
    for path in NESTED_PATHS:
        hit = _first_match(effective, path)
        if hit is not None:
            problems.append(f"line {hit.line}: '{hit.source}' captures the nested path {path}; F-08 covers exactly one slug")
    return problems


def lint_headers(blocks: list[HeaderBlock]) -> list[str]:
    problems: list[str] = []
    effective = headers_for(PROBE_PATH, blocks)
    if "no-store" not in effective.get("cache-control", "").lower():
        problems.append(f"{PROBE_PATH}: Cache-Control must contain no-store")
    if "noindex" not in effective.get("x-robots-tag", "").lower():
        problems.append(f"{PROBE_PATH}: X-Robots-Tag must contain noindex")
    policy = [p.strip().lower() for p in effective.get("referrer-policy", "").split(",") if p.strip()]
    # Browsers apply the last recognised token of a comma-joined list.
    if not policy or policy[-1] != "no-referrer":
        problems.append(f"{PROBE_PATH}: effective Referrer-Policy must end in no-referrer")
    return problems


def load(frontend_dir: Path) -> tuple[list[Redirect], list[HeaderBlock]]:
    for name in ("_redirects", "_headers"):
        if not (frontend_dir / name).is_file():
            raise RulesError(f"{frontend_dir}/{name} is missing")
    redirects = parse_redirects((frontend_dir / "_redirects").read_text(encoding="utf-8"))
    headers = parse_headers((frontend_dir / "_headers").read_text(encoding="utf-8"))
    return redirects, headers


def lint_frontend(frontend_dir: Path) -> list[str]:
    try:
        redirects, headers = load(frontend_dir)
    except RulesError as exc:
        return [str(exc)]
    return lint_redirects(redirects) + lint_public_redirects(redirects) + lint_headers(headers)
