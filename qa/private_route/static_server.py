"""Production-like static server for frontend/ (stdlib only, no Node).

It reads the REAL frontend/_redirects and frontend/_headers and applies only
the Cloudflare Pages subset this QA needs (see pages_rules.py):

  * `_redirects`: 200 rewrites (only when no static asset exists at the path)
    and 3xx redirects, first match wins. Rules Pages silently ignores (splat
    rule to /index[.html], blocker B1) are dropped, as Pages drops them.
  * `_headers`: every matching block applies; repeated headers are joined.
  * Pages HTML handling: `/dir` -> 308 `/dir/`; `/dir/` -> dir/index.html;
    unknown path -> root index.html with 200 (SPA fallback, no 404.html).

It refuses to start if the private-route rewrite is absent or malformed, so it
can never quietly serve a working /c/{token} when the real rules are broken.
Request paths (which carry the bearer token) are never logged.

It is NOT a Cloudflare emulator. QA_SERVER=wrangler is the higher-fidelity
cross-check (docs/QA_PRIVATE_ROUTE.md).

Usage: python -m private_route.static_server --root DIR --port N
"""
from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from private_route import pages_rules as pr

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}
RESERVED = {"_headers", "_redirects"}  # Pages never serves these


class Site:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        redirects, self.header_blocks = pr.load(self.root)
        problems = pr.lint_redirects(redirects) + pr.lint_headers(self.header_blocks)
        if problems:
            raise SystemExit("static_server: refusing to start, routing rules are invalid:\n  - " + "\n  - ".join(problems))
        self.rules, dropped = pr.effective_redirects(redirects)
        self.dropped = dropped

    def _file_for(self, url_path: str) -> Path | None:
        relative = unquote(url_path).lstrip("/")
        if relative in RESERVED:
            return None
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate

    def resolve(self, url_path: str, query: str) -> tuple[str, object]:
        """-> ('redirect', (status, location)) | ('file', Path)."""
        served_path = url_path
        for rule in self.rules:
            if not rule.matches(url_path):
                continue
            if rule.status == 200:
                asset = self._file_for(url_path)
                if asset is not None and asset.is_file():
                    break  # an existing asset wins over a rewrite
                served_path = rule.destination
            else:
                return "redirect", (rule.status, rule.destination)
            break

        target = self._file_for(served_path)
        if target is not None and target.is_dir():
            if not served_path.endswith("/"):
                return "redirect", (308, served_path + "/" + (f"?{query}" if query else ""))
            index = target / "index.html"
            if index.is_file():
                return "file", index
        elif target is not None and target.is_file():
            return "file", target
        return "file", self.root / "index.html"  # Pages SPA fallback


def make_handler(site: Site) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "qa-static"

        def log_message(self, *args, **kwargs) -> None:  # request paths carry the token
            return

        def _serve(self, send_body: bool) -> None:
            path, _, query = self.path.partition("?")
            path = path.split("#", 1)[0]
            kind, value = site.resolve(path, query)
            headers = pr.headers_for(path, site.header_blocks)
            if kind == "redirect":
                status, location = value  # type: ignore[misc]
                self.send_response(status)
                self.send_header("Location", location)
                body = b""
            else:
                file: Path = value  # type: ignore[assignment]
                body = file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", CONTENT_TYPES.get(file.suffix.lower(), "application/octet-stream"))
                headers.setdefault("cache-control", "public, max-age=0, must-revalidate")
            for name, header_value in headers.items():
                self.send_header(name, header_value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._serve(True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve(False)

        def _reject(self) -> None:
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _reject  # noqa: N815

    return Handler


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        print(f"static_server: connection error ({sys.exc_info()[0].__name__})", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    site = Site(args.root)
    print(
        f"static_server: {len(site.rules)} redirect rule(s) applied, {len(site.dropped)} dropped as"
        f" Pages-invalid, {len(site.header_blocks)} header block(s); listening on 127.0.0.1:{args.port}",
        file=sys.stderr,
        flush=True,
    )
    QuietServer(("127.0.0.1", args.port), make_handler(site)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
