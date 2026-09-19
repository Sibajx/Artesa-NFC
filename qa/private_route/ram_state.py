"""RAM-backed state directory for `QA_SERVER=wrangler` (fails closed).

`wrangler pages dev` records the path of every request (so `/c/{token}`) in a
local SQLite store that has no off switch. To keep token-bearing paths off
disk, Wrangler mode requires its state directory to live on RAM-backed
storage (tmpfs/ramfs). There is deliberately NO fallback: when no suitable
location exists, the QA refuses to start (exit 2) instead of writing that
state to a disk-backed directory.

  python -m private_route.ram_state create <base-dir> <run-id>
      creates <base-dir>/artesanfc-qa-route-wrangler-<run-id> (exclusive, 0700),
      verifies it, prints its path. Exit 0, or 2 with a prerequisite message.

The base defaults to /dev/shm (shell: QA_WRANGLER_RAM_DIR overrides it, which
is also the test hook; an override is validated exactly like the default, so it
cannot be used to select disk-backed storage).
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable

RAM_FS_TYPES = frozenset({"tmpfs", "ramfs"})
DIR_PREFIX = "artesanfc-qa-route-wrangler-"

Probe = Callable[[str], "str | None"]


class RamStateError(RuntimeError):
    """RAM-backed state storage is unavailable or unsuitable."""


def fs_type(path: str) -> str | None:
    """Filesystem type name of `path` (Linux `stat -f`), or None if unknown."""
    try:
        result = subprocess.run(
            ["stat", "-f", "-c", "%T", path], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _refuse(reason: str) -> RamStateError:
    return RamStateError(
        "QA_SERVER=wrangler requires RAM-backed state storage because Wrangler records request paths "
        "(including /c/{token}) in a local store with no off switch, and this QA never lets that state "
        f"reach a disk-backed directory. Problem: {reason}. Use /dev/shm (tmpfs), or set "
        "QA_WRANGLER_RAM_DIR to a writable tmpfs/ramfs directory. Failing closed; nothing was started."
    )


def validate_base(base: str, probe: Probe = fs_type) -> None:
    path = Path(base)
    if not path.is_dir():
        raise _refuse(f"'{base}' does not exist or is not a directory")
    if not os.access(base, os.W_OK | os.X_OK):
        raise _refuse(f"'{base}' is not writable")
    kind = probe(base)
    if kind not in RAM_FS_TYPES:
        raise _refuse(f"'{base}' is on '{kind or 'unknown'}' storage, not tmpfs/ramfs")


def create_state_dir(base: str, run_id: str, probe: Probe = fs_type) -> Path:
    validate_base(base, probe)
    state = Path(base) / f"{DIR_PREFIX}{run_id}"
    try:
        state.mkdir(mode=0o700)  # exclusive: an existing dir is an error, so it is unique to this run
    except FileExistsError:
        raise _refuse(f"'{state}' already exists (state directories are unique per run)") from None
    except OSError as exc:
        raise _refuse(f"cannot create '{state}' ({type(exc).__name__})") from None
    problems = verify_state_dir(state, probe)
    if problems:
        shutil.rmtree(state, ignore_errors=True)
        raise _refuse("; ".join(problems))
    return state


def verify_state_dir(state: Path, probe: Probe = fs_type) -> list[str]:
    """Problems with an existing state dir (empty list = ok): exists, is a directory,
    private (0700), writable (a probe file is written and removed), RAM-backed."""
    problems: list[str] = []
    if not state.is_dir():
        return [f"state directory {state.name} does not exist"]
    if not state.name.startswith(DIR_PREFIX):
        problems.append("state directory name is not the per-run unique name")
    if stat.S_IMODE(state.stat().st_mode) & 0o077:
        problems.append("state directory is accessible by other users")
    try:
        marker = state / ".qa-write-probe"
        marker.write_bytes(b"x")
        marker.unlink()
    except OSError:
        problems.append("state directory is not writable")
    kind = probe(str(state))
    if kind not in RAM_FS_TYPES:
        problems.append(f"state directory is on '{kind or 'unknown'}' storage, not tmpfs/ramfs")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] != "create":
        print("usage: python -m private_route.ram_state create <base-dir> <run-id>", file=sys.stderr)
        return 2
    try:
        print(create_state_dir(argv[2], argv[3]))
    except RamStateError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
