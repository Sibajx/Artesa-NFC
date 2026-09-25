#!/usr/bin/env python3
"""Build an immutable, reproducible backend release artifact (N-08, ADR-027).

The artifact is assembled from **git objects** (``git ls-tree`` +
``git cat-file``), never from the working tree, so untracked, ignored or
modified files -- a stray ``.env``, a local ``main.py`` -- cannot leak in.
Only an explicit allowlist of roots is packaged.

    build_release.py [--ref origin/main] [--out DIR]

Production builds refuse any commit that is not reachable from
``refs/remotes/origin/main``. ``--rehearsal`` lifts that for local rehearsal
and stamps the artifact ``channel: rehearsal``; the deploy tool refuses such an
artifact unless it is pointed at a non-production root.

The builder refuses a checkout with modified tracked files (the artifact never
reads the working tree, but a dirty tree means the operator may believe they
are shipping something they are not). ``--allow-dirty`` exists for rehearsal
builds only.

Reproducibility: ``built_at_utc`` (and the release id) follow the
SOURCE_DATE_EPOCH convention. By default they are the commit time, so building
the same commit twice with the same builder yields byte-identical artifacts.
Git tags are deliberately NOT part of RELEASE.json: creating, moving or
deleting a tag must not change the artifact. The builder only prints them.

Run ``git fetch origin`` first: the reachability check reads the local
remote-tracking ref and never touches the network (a stale ref can only make
the check stricter).
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_common as rc  # noqa: E402

FILE_MODE = 0o644
_DOCS_ENABLED_RE = re.compile(rb"def\s+docs_enabled\s*\(")
_BODY_LIMIT_RE = re.compile(rb"^_RESOLVE_MAX_BODY_BYTES\s*=\s*([0-9_]+)\s*$", re.MULTILINE)


class BuildResult:
    def __init__(self, release_id: str, artifact: Path, sidecar: Path, release: dict, manifest: bytes) -> None:
        self.release_id = release_id
        self.artifact = artifact
        self.sidecar = sidecar
        self.release = release
        self.manifest = manifest


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"git {args[0]} failed: {proc.stderr.decode(errors='replace').strip()[:160]}")
    return proc.stdout


def assert_clean_worktree(repo: Path) -> None:
    """Tracked files must match HEAD (untracked files are ignored: git objects
    are the only input, and untracked files cannot reach the artifact)."""
    status = _git(repo, "status", "--porcelain", "--untracked-files=no")
    if status.strip():
        raise rc.OpsError(
            rc.Exit.ARTIFACT_INVALID,
            "working tree has modified tracked files; commit or stash them (the artifact is built from git objects only)",
        )


def resolve_commit(repo: Path, ref: str) -> str:
    if not ref or ref.startswith("-") or any(ch in ref for ch in "\0\n\r "):
        raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, "invalid git ref")
    try:
        out = _git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()
    except rc.OpsError:
        raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, "git ref does not resolve to a commit") from None
    if not re.fullmatch(r"[0-9a-f]{40}", out):
        raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, "git ref did not resolve to a full commit id")
    return out


def assert_reachable_from_origin_main(repo: Path, commit: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, rc.PRODUCTION_REF_FULL],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode == 1:
        raise rc.OpsError(
            rc.Exit.ARTIFACT_INVALID,
            f"commit {commit[:12]} is not reachable from {rc.PRODUCTION_REF}; production artifacts are built "
            "only from merged, reviewed history (use --rehearsal for local rehearsal)",
        )
    if proc.returncode != 0:
        raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"cannot evaluate {rc.PRODUCTION_REF_FULL} (run 'git fetch origin')")


def read_tree_files(repo: Path, commit: str, subdir: str = "backend") -> dict[str, bytes]:
    """Allowlisted files of ``<commit>:<subdir>``, read straight from the
    object database. Refuses symlinks, submodules and secret-shaped names."""
    listing = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit, "--", subdir)
    wanted: dict[str, str] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        mode, kind, sha = meta.decode("ascii").split()
        path = raw_path.decode("utf-8", "surrogateescape")
        rel = path[len(subdir) + 1 :]
        if not rc.is_allowed_content_path(rel):
            continue
        if kind != "blob" or mode not in ("100644", "100755"):
            raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"non-regular tracked entry inside the artifact roots: {rc._short(rel)}")
        rc.validate_member_path(rel)
        wanted[rel] = sha

    contents: dict[str, bytes] = {}
    if wanted:
        request = "".join(f"{sha}\n" for sha in wanted.values()).encode("ascii")
        raw = _git(repo, "cat-file", "--batch", input_bytes=request)
        stream = io.BytesIO(raw)
        for rel, sha in wanted.items():
            header = stream.readline().split()
            if len(header) != 3 or header[0].decode() != sha or header[1] != b"blob":
                raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, "unexpected git cat-file output")
            size = int(header[2])
            if size > rc.MAX_FILE_BYTES:
                raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"file too large for an artifact: {rc._short(rel)}")
            contents[rel] = stream.read(size)
            stream.read(1)  # trailing LF
            rc.assert_no_secret_content(rel, contents[rel])
    return contents


def _detect_runtime(files: dict[str, bytes]) -> dict:
    config = files.get("app/core/config.py", b"")
    main = files.get("app/main.py", b"")
    limit = _BODY_LIMIT_RE.search(main)
    return {
        "app_env_required": "production",
        "docs_in_production": not bool(_DOCS_ENABLED_RE.search(config)),
        "resolve_body_limit_bytes": int(limit.group(1).replace(b"_", b"")) if limit else None,
    }


def tags_for(repo: Path, commit: str) -> list[str]:
    """Tags pointing exactly at ``commit``. Informational only: printed by the
    builder, never written into the artifact (a tag created or moved later
    must not change the bytes built from the same commit)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "tag", "--points-at", commit],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return sorted(t for t in proc.stdout.decode(errors="replace").split() if re.fullmatch(r"[A-Za-z0-9._/-]{1,64}", t))


def _write_tar(entries: dict[str, bytes], mtime: int) -> bytes:
    """Deterministic ustar+gzip: sorted names, fixed mtime, root:root, fixed
    modes, no gzip name/timestamp."""
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            for name in sorted(entries):
                info = tarfile.TarInfo(name)
                info.size = len(entries[name])
                info.mtime = mtime
                info.mode = FILE_MODE
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.type = tarfile.REGTYPE
                tar.addfile(info, io.BytesIO(entries[name]))
    return raw.getvalue()


def build_release(
    repo: Path,
    out_dir: Path,
    *,
    ref: str = rc.PRODUCTION_REF,
    rehearsal: bool = False,
    target_python: str = "3.14",
    release_time: datetime | None = None,
    allow_dirty: bool = False,
) -> BuildResult:
    repo = Path(repo)
    if allow_dirty and not rehearsal:
        raise rc.OpsError(rc.Exit.USAGE, "--allow-dirty is only accepted together with --rehearsal")
    if not allow_dirty:
        assert_clean_worktree(repo)
    commit = resolve_commit(repo, ref)
    if not rehearsal:
        assert_reachable_from_origin_main(repo, commit)
    tree = _git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    committed = int(_git(repo, "show", "-s", "--format=%ct", commit).decode().strip())

    files = read_tree_files(repo, commit)
    missing = [path for path in rc.REQUIRED_FILES if path not in files]
    if missing:
        raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, "required file(s) missing from the commit: " + ", ".join(missing))

    lock_text = files[rc.LOCK_PATH].decode("utf-8")
    rc.parse_lock(lock_text, target_python=target_python)

    version_files = {path: data for path, data in files.items() if path.startswith("alembic/versions/")}
    revisions = rc.parse_alembic_revisions(version_files)
    heads = rc.alembic_heads(revisions)
    if len(heads) != 1:
        raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"Alembic must have exactly one head (found {len(heads)})")
    ledger = rc.parse_ledger(files[rc.LEDGER_PATH])
    rc.check_ledger_complete(revisions, ledger)

    manifest_entries = {path: rc.sha256_bytes(data) for path, data in files.items()}
    manifest = rc.render_manifest(manifest_entries)

    if release_time is not None:
        now, timestamp_source = release_time, "source_date_epoch"
    else:
        now, timestamp_source = datetime.fromtimestamp(committed, timezone.utc), "commit"
    release_id = rc.format_release_id(now, commit)
    release = {
        "schema_version": rc.RELEASE_SCHEMA_VERSION,
        "project": rc.PROJECT,
        "release_id": release_id,
        "channel": rc.CHANNEL_REHEARSAL if rehearsal else rc.CHANNEL_PRODUCTION,
        "git": {
            "commit": commit,
            "commit_short": commit[:12],
            "tree": tree,
            "committed_at": rc.utc_iso(datetime.fromtimestamp(committed, timezone.utc)),
            "reachable_from": None if rehearsal else rc.PRODUCTION_REF,
        },
        "build": {
            "built_at_utc": rc.utc_iso(now),
            "timestamp_source": timestamp_source,
            "builder_version": rc.BUILDER_VERSION,
            "target_python": target_python,
        },
        "artifact": {
            "roots": sorted([*rc.ARTIFACT_ROOT_DIRS, *rc.ARTIFACT_ROOT_FILES]),
            "file_count": len(files),
            "content_sha256": rc.sha256_bytes(manifest),
        },
        "deps": {
            "lockfile": rc.LOCK_PATH,
            "lockfile_sha256": rc.sha256_bytes(files[rc.LOCK_PATH]),
            "install_mode": rc.INSTALL_MODE,
        },
        "alembic": {"head": heads[0], "heads_count": 1, "revisions": revisions},
        "runtime": _detect_runtime(files),
    }
    rc.validate_release_json(release, expected_id=release_id)

    tar_bytes = _write_tar(
        {**files, "MANIFEST.sha256": manifest, "RELEASE.json": rc.canonical_json(release)}, committed
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / rc.artifact_filename(release_id)
    sidecar = artifact.with_name(artifact.name + ".sha256")
    for target, payload in (
        (artifact, tar_bytes),
        (sidecar, f"{rc.sha256_bytes(tar_bytes)}  {artifact.name}\n".encode("ascii")),
    ):
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)  # never overwrite a release
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
    return BuildResult(release_id, artifact, sidecar, release, manifest)


def _source_date_epoch(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if not re.fullmatch(r"[0-9]{1,12}", value):
        raise rc.OpsError(rc.Exit.USAGE, "SOURCE_DATE_EPOCH must be a non-negative integer")
    return datetime.fromtimestamp(int(value), timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".", help="git repository (default: current directory)")
    parser.add_argument("--ref", default=rc.PRODUCTION_REF, help="commit-ish to build (default: origin/main)")
    parser.add_argument("--out", default="dist", help="output directory")
    parser.add_argument("--target-python", default="3.14")
    parser.add_argument("--rehearsal", action="store_true", help="allow a commit outside origin/main; never deployable to production")
    parser.add_argument("--allow-dirty", action="store_true", help="rehearsal only: accept modified tracked files")
    args = parser.parse_args(argv)
    try:
        release_time = _source_date_epoch(os.environ.get("SOURCE_DATE_EPOCH"))
        result = build_release(
            Path(args.repo), Path(args.out), ref=args.ref, rehearsal=args.rehearsal, target_python=args.target_python,
            release_time=release_time, allow_dirty=args.allow_dirty,
        )
    except rc.OpsError as exc:
        print(f"build failed: {exc.message}", file=sys.stderr)
        return int(exc.code)
    print(f"release_id      {result.release_id}")
    print(f"channel         {result.release['channel']}")
    print(f"commit          {result.release['git']['commit']}")
    print(f"tags            {' '.join(tags_for(Path(args.repo), result.release['git']['commit'])) or '-'}  (informational, not in the artifact)")
    print(f"files           {result.release['artifact']['file_count']}")
    print(f"content_sha256  {result.release['artifact']['content_sha256']}")
    print(f"artifact_sha256 {result.sidecar.read_text().split()[0]}")
    print(f"artifact        {result.artifact}")
    print(f"sidecar         {result.sidecar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
