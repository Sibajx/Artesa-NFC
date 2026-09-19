"""Step 1 of qa/validate-private-route.sh: needs no Docker, browser or backend.

  a) harness self-tests: proves the linter and the secret scrubber can FAIL
     (negative controls), so a green run means something;
  b) lints the REAL frontend/_redirects and frontend/_headers.

Usage: python -m private_route.static_checks <frontend-dir>
Exit:  0 = all pass, 1 = a check failed.
"""
from __future__ import annotations

import sys
from pathlib import Path

from private_route import pages_rules as pr
from private_route import ram_state as rs
from private_route import routing_checks as rc
from private_route.common import Report, Secrets, format_exception, SECRETS
from private_route.static_server import Site

GOOD_REDIRECTS = "# comment\n/c/*  /c/  200\n"
GOOD_PUBLIC = (
    "/piezas/:slug  /_shell/pieza/  200\n"
    "/piezas/:slug/  /_shell/pieza/  200\n"
    "/artesanos/:slug  /_shell/artesano/  200\n"
    "/artesanos/:slug/  /_shell/artesano/  200\n"
)
GOOD_HEADERS = (
    "/*\n  Referrer-Policy: strict-origin-when-cross-origin\n"
    "/c/*\n  Cache-Control: no-store\n  X-Robots-Tag: noindex, nofollow\n  Referrer-Policy: no-referrer\n"
)


def _lint(redirects: str, headers: str = GOOD_HEADERS) -> list[str]:
    return pr.lint_redirects(pr.parse_redirects(redirects)) + pr.lint_headers(pr.parse_headers(headers))


def _lint_public(redirects: str) -> list[str]:
    return pr.lint_public_redirects(pr.parse_redirects(redirects))


def public_rules_self_tests(report: Report) -> None:
    """Negative controls for the F-08 public entity-shell rules, the placeholder
    grammar and the precedence model. Everything is proven on synthetic trees
    and rule texts, so a green run means the checks can fail."""
    import tempfile

    report.section("Harness self-tests: public entity-shell rules (F-08), placeholders and precedence")

    report.check(_lint_public(GOOD_REDIRECTS + GOOD_PUBLIC) == [], "linter accepts exactly the four approved public rules")
    report.check(_lint_public(GOOD_PUBLIC + GOOD_REDIRECTS) == [], "rule order between the private and public groups is irrelevant")

    splat = _lint_public(GOOD_REDIRECTS + GOOD_PUBLIC.replace("/piezas/:slug/  ", "/piezas/*  ", 1))
    report.check(
        any("rewrites the list route /piezas/" in p for p in splat),
        "linter rejects the splat form '/piezas/*' that Wrangler 4.135.0 showed capturing the list page",
        "; ".join(splat) or "no problem reported",
    )
    report.check(
        any("/piezas" in p and "list route" in p for p in _lint_public(GOOD_REDIRECTS + GOOD_PUBLIC + "/piezas/*  /_shell/pieza/  200\n")),
        "linter rejects a splat rule added after the good ones for the list route",
    )
    report.check(bool(_lint_public(GOOD_REDIRECTS + GOOD_PUBLIC.replace("/piezas/:slug/  /_shell/pieza/  200\n", ""))), "linter rejects a missing trailing-slash rule (Pages does not match /piezas/x for '/piezas/:slug/' nor the reverse)")
    report.check(bool(_lint_public(GOOD_REDIRECTS + GOOD_PUBLIC.replace("/artesanos/:slug  /_shell/artesano/  200\n", ""))), "linter rejects a missing no-slash rule")
    report.check(bool(_lint_public(GOOD_REDIRECTS + GOOD_PUBLIC.replace("/_shell/pieza/", "/piezas/detalle/"))), "linter rejects a shell destination inside the /piezas/ namespace")
    report.check(bool(_lint_public(GOOD_REDIRECTS + GOOD_PUBLIC.replace("200", "302", 1))), "linter rejects the right rule with the wrong status")
    report.check(bool(_lint_public(GOOD_REDIRECTS + GOOD_PUBLIC + GOOD_PUBLIC)), "linter rejects duplicated public rules")
    report.check(
        any("nested path" in p for p in _lint_public(GOOD_REDIRECTS + GOOD_PUBLIC + "/piezas/:a/:b  /_shell/pieza/  200\n")),
        "linter rejects a rule that captures nested paths (F-08 covers exactly one slug)",
    )
    report.check(bool(_lint_public(GOOD_REDIRECTS + "/piezas/:slug  /_shell/pieza/  200\n/artesanos/:slug  /_shell/artesano/  200\n")), "linter rejects the no-slash-only form")

    # placeholder grammar (Wrangler 4.135.0: one non-empty segment, exact)
    single = pr._regex("/piezas/:slug")
    report.check(
        bool(single.match("/piezas/x")) and bool(single.match("/piezas/index.html")) and bool(single.match("/piezas/a%2Fb"))
        and not single.match("/piezas/") and not single.match("/piezas") and not single.match("/piezas/x/") and not single.match("/piezas/a/b") and not single.match("/Piezas/x"),
        "placeholder '/piezas/:slug' matches one non-empty segment only (not '/piezas/', not a trailing slash, not nested, case-sensitive)",
    )
    slashed = pr._regex("/piezas/:slug/")
    report.check(bool(slashed.match("/piezas/x/")) and not slashed.match("/piezas/x") and not slashed.match("/piezas//"), "placeholder '/piezas/:slug/' matches only the trailing-slash form")
    report.check(bool(pr._regex("/piezas/*").match("/piezas/")) and bool(pr._regex("/c/*").match("/c/a/b")), "splat '*' matches the empty string and crosses '/'")
    report.check(pr.parse_redirects("/piezas/:slug  /_shell/pieza/  200\n")[0].source == "/piezas/:slug", "parser accepts a placeholder in a source")
    try:
        pr.parse_redirects("/c/*  /c/:token  200\n")
        report.bad("parser refuses a placeholder substitution in a destination")
    except pr.RulesError:
        report.ok("parser refuses a placeholder substitution in a destination")

    # precedence model on synthetic trees
    def build(root, redirects: str) -> None:
        for name, title in {
            "index.html": "Home", "c/index.html": "Cert", "piezas/index.html": "Lista piezas", "artesanos/index.html": "Lista artesanos",
            "_shell/pieza/index.html": "Shell pieza", "_shell/artesano/index.html": "Shell artesano", "piezas/legacy/index.html": "Legacy",
        }.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"<title>{title}</title>")
        (root / "_redirects").write_text(redirects)
        (root / "_headers").write_text(GOOD_HEADERS)

    from pathlib import Path

    with tempfile.TemporaryDirectory() as raw:
        good = Path(raw) / "good"
        build(good, GOOD_REDIRECTS + GOOD_PUBLIC)
        site = Site(good)

        def served(path: str) -> str:
            kind, value = site.resolve(path, "")
            return f"redirect {value[0]}" if kind == "redirect" else Path(value).parent.relative_to(good).as_posix() or "."  # type: ignore[arg-type]

        report.check(served("/piezas/") == "piezas" and served("/artesanos/") == "artesanos", "model: the list pages are served, not captured")
        report.check(served("/piezas/x/") == "_shell/pieza" and served("/piezas/x") == "_shell/pieza", "model: one slug (either form) -> the pieza shell")
        report.check(served("/piezas/legacy") == "_shell/pieza", "model: a slug that has a legacy static folder is still captured (the rewrite beats an existing page)")
        report.check(served("/piezas/index.html") == "_shell/pieza", "model: a 200 rewrite wins over an EXISTING FILE (Wrangler 4.135.0; the old 'no asset' assumption is gone)")
        report.check(served("/piezas/a/b/") == ".", "model: nested paths fall through to the SPA fallback (Home)")
        report.check(served("/piezas") == "redirect 308" and served("/c") == "redirect 308", "model: '/dir' -> 308 '/dir/'")
        report.group("model: the whole ROUTE_MATRIX holds on a synthetic tree with the good rules", [p for p in rc.matrix_problems_model(site) if "assets/" not in p])

        old = Path(raw) / "old"
        build(old, GOOD_REDIRECTS + "/piezas/*  /_shell/pieza/  200\n/artesanos/*  /_shell/artesano/  200\n")
        try:
            Site(old)
            report.bad("server refuses to start with the capturing splat rules")
        except SystemExit:
            report.ok("server refuses to start with the capturing splat rules (lint)")
        unlinted = Site(old, lint=False)
        kind, value = unlinted.resolve("/piezas/", "")
        report.check(
            kind == "file" and Path(value).parent.relative_to(unlinted.root).as_posix() == "_shell/pieza",  # type: ignore[arg-type]
            "negative control: with '/piezas/*' the model, like Wrangler, serves the shell for '/piezas/' (the list would be lost)",
        )
        report.check(bool(rc.matrix_problems_model(unlinted)), "negative control: ROUTE_MATRIX fails against that capturing rule set")


def self_tests(report: Report) -> None:
    report.section("Harness self-tests (negative controls)")

    report.check(_lint(GOOD_REDIRECTS) == [], "linter accepts '/c/*  /c/  200' with the private headers")

    b1 = _lint("/c/*  /c/index.html  200\n")
    report.check(
        any("blocker B1" in p for p in b1) and any("private route rule is" in p for p in b1),
        "linter rejects the previously broken B1 form '/c/*  /c/index.html  200'",
        "; ".join(b1) or "no problem reported",
    )
    report.check(bool(_lint("/c/*  /index.html  200\n")), "linter rejects any splat rule to /index.html")
    report.check(bool(_lint("")), "linter rejects a missing private-route rule")
    report.check(bool(_lint("/c/*  /c/  302\n")), "linter rejects the right rule with the wrong status")
    report.check(
        bool(_lint("/c/*  /c/  200\n/c/*  /c/  200\n")), "linter rejects a duplicated private-route rule"
    )
    report.check(
        _lint("/c/x  /elsewhere  301\n/c/*  /c/  200\n") == [],
        "linter tolerates unrelated rules that do not touch /c/{token}",
    )
    report.check(
        bool(_lint("/*  /home  301\n/c/*  /c/  200\n")),
        "linter rejects an earlier rule that would capture /c/{token}",
    )

    for label, text in {
        "a line with one field": "/c/*\n",
        "a non-numeric status": "/c/*  /c/  ok\n",
        "an unsupported status": "/c/*  /c/  418\n",
        "a placeholder substitution in a destination": "/c/*  /c/:token  200\n",
    }.items():
        try:
            pr.parse_redirects(text)
            report.bad(f"parser refuses {label}")
        except pr.RulesError:
            report.ok(f"parser refuses {label}")

    weak_headers = "/c/*\n  Cache-Control: max-age=60\n"
    report.check(len(pr.lint_headers(pr.parse_headers(weak_headers))) == 3, "header lint flags missing no-store/noindex/no-referrer")
    joined = pr.headers_for("/c/x", pr.parse_headers(GOOD_HEADERS))
    report.check(
        joined.get("referrer-policy") == "strict-origin-when-cross-origin, no-referrer",
        "header join matches Pages (global + /c/* Referrer-Policy joined with ', ')",
    )

    secrets = Secrets()
    secrets.add("TOKEN", "valid", "S3cr3tTokenValue_abc-123")
    secrets.add("HASH", "valid", "deadbeef" * 8)
    try:
        raise RuntimeError("request to /c/S3cr3tTokenValue_abc-123 failed; hash " + "deadbeef" * 8)
    except RuntimeError as exc:
        import traceback

        text = secrets.scrub("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    report.check(
        "S3cr3tTokenValue_abc-123" not in text
        and "deadbeef" * 8 not in text
        and "<TOKEN:valid>" in text
        and "<HASH:valid>" in text
        and "RuntimeError" in text
        and "Traceback" in text,
        "exception text is scrubbed of token/hash but keeps its type and traceback",
    )
    report.check(secrets.find("x S3cr3tTokenValue_abc-123 y") == ["<TOKEN:valid>"], "secret finder detects a planted token")
    report.check(
        "S3cr3tTokenValue_abc-123" not in repr(secrets), "the secret registry never shows values in repr()"
    )
    assert SECRETS.find("nothing") == []  # module-level registry starts empty


def ram_state_self_tests(report: Report) -> None:
    """Negative controls for Wrangler mode's fail-closed RAM-backed state rule.
    The filesystem probe is injected, so nothing on the host is modified."""
    import os
    import tempfile

    report.section("Harness self-tests: Wrangler state must be RAM-backed (fail closed)")
    ram, disk = (lambda _p: "tmpfs"), (lambda _p: "ext4")

    def refuses(action) -> bool:
        try:
            action()
        except rs.RamStateError as exc:
            return "requires RAM-backed state storage" in str(exc) and "Failing closed" in str(exc)
        return False

    with tempfile.TemporaryDirectory() as base:
        report.check(refuses(lambda: rs.validate_base(os.path.join(base, "missing"), ram)), "refuses a base directory that does not exist")
        report.check(refuses(lambda: rs.validate_base(base, disk)), "refuses a disk-backed base (ext4): no fallback to disk")
        report.check(refuses(lambda: rs.validate_base(base, lambda _p: None)), "refuses a base whose filesystem type cannot be determined")
        report.check(refuses(lambda: rs.create_state_dir(base, "r1", disk)) and not os.listdir(base), "a refused base creates no directory")
        state = rs.create_state_dir(base, "r1", ram)
        report.check(state.name == rs.DIR_PREFIX + "r1" and oct(state.stat().st_mode & 0o777) == "0o700", "creates a unique, private (0700) state directory")
        report.check(rs.verify_state_dir(state, ram) == [] and not (state / ".qa-write-probe").exists(), "verifies it: exists, writable, RAM-backed (probe file cleaned)")
        report.check(refuses(lambda: rs.create_state_dir(base, "r1", ram)), "refuses to reuse an existing state directory (unique per run)")
        report.check(bool(rs.verify_state_dir(state, disk)), "verification flags a state directory that is not RAM-backed")
        os.chmod(base, 0o500)
        try:
            report.check(os.geteuid() == 0 or refuses(lambda: rs.validate_base(base, ram)), "refuses a base directory that is not writable")
        finally:
            os.chmod(base, 0o700)


def real_rules(report: Report, frontend_dir: Path) -> None:
    report.section(f"Real routing rules: {frontend_dir.name}/_redirects and _headers")
    try:
        redirects, headers = pr.load(frontend_dir)
    except pr.RulesError as exc:
        report.bad(f"cannot read routing files: {exc}")
        return
    report.group("_redirects: private route rule is exactly '/c/*  /c/  200' and not Pages-ignored", pr.lint_redirects(redirects))
    public = pr.lint_public_redirects(redirects)
    report.group("_redirects: public entity routes are exactly the four /:slug rules to /_shell/, lists uncaptured, nested uncaptured", public)
    report.group("_headers: /c/* is no-store, noindex and effectively no-referrer", pr.lint_headers(headers))
    if not public and not pr.lint_redirects(redirects) and not pr.lint_headers(headers):
        report.group(
            f"built-in Pages model over the REAL frontend/: every ROUTE_MATRIX path resolves as expected ({len(rc.ROUTE_MATRIX)} paths)",
            rc.matrix_problems_model(Site(frontend_dir)),
        )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m private_route.static_checks <frontend-dir>", file=sys.stderr)
        return 2
    report = Report()
    try:
        self_tests(report)
        public_rules_self_tests(report)
        ram_state_self_tests(report)
        real_rules(report, Path(argv[1]).resolve())
    except Exception as exc:  # noqa: BLE001
        print(format_exception(exc), file=sys.stderr)
        return 2
    print(f"\nstatic checks: {report.passed} passed, {report.failed} failed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
