"""Secret scrubbing and reporting shared by every QA module.

The synthetic certificate tokens (and their hashes) exist only in this
process's memory. Everything that can reach a terminal, a log or an exception
message goes through `scrub()`, which replaces each known secret value with an
alias such as <TOKEN:valid>. Nothing here ever writes a secret to disk.
"""
from __future__ import annotations

import sys
import traceback
from urllib.parse import quote


class Secrets:
    """Registry of secret values -> printable placeholders."""

    def __init__(self) -> None:
        self._items: list[tuple[str, str]] = []  # (value, placeholder)

    def add(self, kind: str, alias: str, value: str) -> None:
        placeholder = f"<{kind}:{alias}>"
        for variant in {value, quote(value, safe="")}:
            if variant and not any(v == variant for v, _ in self._items):
                self._items.append((variant, placeholder))
        # Longest first so a value that contains another is replaced whole.
        self._items.sort(key=lambda item: len(item[0]), reverse=True)

    def scrub(self, text: object) -> str:
        out = str(text)
        for value, placeholder in self._items:
            out = out.replace(value, placeholder)
        return out

    def find(self, data: str | bytes) -> list[str]:
        """Placeholders of every registered secret contained in `data`."""
        found: list[str] = []
        for value, placeholder in self._items:
            hit = value.encode() in data if isinstance(data, bytes) else value in data
            if hit and placeholder not in found:
                found.append(placeholder)
        return found

    def __repr__(self) -> str:  # never list the values
        return f"<Secrets n={len(self._items)}>"


SECRETS = Secrets()


def scrub(text: object) -> str:
    return SECRETS.scrub(text)


def format_exception(exc: BaseException) -> str:
    """Full traceback text (type, chain and frames kept) with secrets replaced."""
    return scrub("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))


class Report:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    @staticmethod
    def _emit(line: str) -> None:
        print(scrub(line), flush=True)

    def section(self, title: str) -> None:
        self._emit(f"\n== {title} ==")

    def ok(self, message: str) -> None:
        self.passed += 1
        self._emit(f"  PASS  {message}")

    def bad(self, message: str, details: list[str] | None = None) -> None:
        self.failed += 1
        self._emit(f"  FAIL  {message}")
        for detail in details or []:
            self._emit(f"          - {detail}")

    def note(self, message: str) -> None:
        self._emit(f"        {message}")

    def check(self, condition: bool, message: str, detail: str = "") -> bool:
        if condition:
            self.ok(message)
        else:
            self.bad(message + (f" ({detail})" if detail else ""))
        return bool(condition)

    def group(self, message: str, problems: list[str]) -> bool:
        """One line for a group of assertions: PASS, or FAIL with each problem."""
        if problems:
            self.bad(message, problems)
            return False
        self.ok(message)
        return True


def install_excepthook() -> None:
    def hook(exc_type, exc, tb):  # noqa: ANN001
        sys.stderr.write(scrub("".join(traceback.format_exception(exc_type, exc, tb))))

    sys.excepthook = hook


def scan_tree(root, max_bytes: int = 50_000_000) -> tuple[int, list[str]]:
    """Search every regular file under `root` for any registered secret. There
    is no exemption mechanism on purpose. Returns (files scanned, problems);
    only aliases are ever reported."""
    from pathlib import Path

    base = Path(root)
    scanned = 0
    problems: list[str] = []
    try:
        paths = sorted(base.rglob("*"))
    except OSError:
        paths = []
    for path in paths:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        scanned += 1
        hits = SECRETS.find(data)
        if hits:
            problems.append(f"{path.relative_to(base)} contains {', '.join(hits)}")
    return scanned, problems
