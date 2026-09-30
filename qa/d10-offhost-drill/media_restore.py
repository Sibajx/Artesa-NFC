#!/usr/bin/env python3
"""Recover the media originals from their off-host copies (M3, docs/BACKUP.md §16).

Runs on the operator's trusted machine, NEVER on easerver: it needs a private
age identity (K1 or K2). Inputs, all copied from the bucket beforehand:

  * one database backup (``bundle.tar.age`` + ``meta.json``): its encrypted
    bundle carries ``media-index.json``, the map ``path -> sha256`` of every
    original at that backup's time;
  * the objects ``media/originales/<sha256>.age`` (e.g. ``b2 sync``).

For every file of the index it decrypts ``<sha256>.age`` into
``<out_dir>/<path>`` and checks that the result's SHA-256 is the one in the
index. A missing object or a mismatch is reported and makes the run fail; the
rest is still recovered.

The identity is decrypted ONCE (one passphrase prompt) into a private 0700
temp dir and removed on exit, also on failure or Ctrl-C; the bundle's
plaintext too. ``<out_dir>`` must not exist yet: nothing is overwritten.

    python3 qa/d10-offhost-drill/media_restore.py <backup_dir> <identity.key.age> <objects_dir> <out_dir> [--sample N]

``--sample N`` recovers only N files (the drill: proves the chain without
downloading everything). Exit: 0 = every requested file recovered and
verified, 1 = something missing or different, 2 = setup problem.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

EXIT_OK, EXIT_FAIL, EXIT_SETUP = 0, 1, 2
INDEX_NAME = "media-index.json"


def say(message: str) -> None:
    print(message, flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def age(argv: list[str], binary: str, *, interactive: bool = False) -> None:
    try:
        result = subprocess.run([binary, *argv], stdin=None if interactive else subprocess.DEVNULL,
                                stderr=None if interactive else subprocess.PIPE, timeout=600)
    except FileNotFoundError:
        raise SystemExit(f"{binary} not found") from None
    if result.returncode != 0:
        reason = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"age {argv[0]} failed (exit {result.returncode}) {reason[0][:200]}")


def safe_target(out_dir: Path, rel: str) -> Path:
    parts = PurePosixPath(rel).parts
    if not parts or rel.startswith("/") or ".." in parts or any(p.startswith(".") for p in parts):
        raise ValueError(f"unsafe path in the index: {rel!r}")
    return out_dir.joinpath(*parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("backup_dir", type=Path)
    parser.add_argument("identity", type=Path)
    parser.add_argument("objects_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--sample", type=int, default=0)
    args = parser.parse_args(argv)
    binary = os.environ.get("AGE", "age")

    for path in (args.backup_dir / "bundle.tar.age", args.identity, args.objects_dir):
        if not path.exists():
            say(f"SETUP: {path} does not exist")
            return EXIT_SETUP
    if args.out_dir.exists():
        say(f"SETUP: {args.out_dir} already exists; choose a new directory (nothing is overwritten)")
        return EXIT_SETUP

    work = Path(tempfile.mkdtemp(prefix="artesa-media-restore-"))
    os.chmod(work, 0o700)
    try:
        key = work / "identity"
        if args.identity.read_bytes().startswith(b"age-encryption.org/v1\n"):
            say(f"decrypting {args.identity.name} (asks for its passphrase once)")
            age(["--decrypt", "-o", str(key), str(args.identity)], binary, interactive=True)
        else:
            shutil.copyfile(args.identity, key)
        os.chmod(key, 0o600)

        bundle = work / "bundle.tar"
        age(["--decrypt", "-i", str(key), "-o", str(bundle), str(args.backup_dir / "bundle.tar.age")], binary)
        with tarfile.open(bundle) as tar:
            try:
                member = tar.getmember(INDEX_NAME)
            except KeyError:
                say(f"FAIL: this backup has no {INDEX_NAME} (made before M3, or without MEDIA_ROOT)")
                return EXIT_FAIL
            index = json.loads(tar.extractfile(member).read())
        bundle.unlink()
        files = index["files"]
        say(f"index: {len(files)} original(s), {index['totals']['bytes']} bytes, created {index['created_at']}")
        if args.sample:
            files = files[: args.sample]

        args.out_dir.mkdir(parents=True, mode=0o700)
        missing, different, recovered = [], [], 0
        for entry in files:
            source = args.objects_dir / f"{entry['sha256']}.age"
            target = safe_target(args.out_dir, entry["path"])
            if not source.is_file():
                missing.append(entry["path"])
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            age(["--decrypt", "-i", str(key), "-o", str(target), str(source)], binary)
            os.chmod(target, 0o600)
            if sha256_file(target) != entry["sha256"]:
                different.append(entry["path"])
                continue
            recovered += 1
        say(f"recovered and verified {recovered}/{len(files)} into {args.out_dir}")
        for path in missing[:20]:
            say(f"  MISSING object for {path}")
        for path in different[:20]:
            say(f"  SHA-256 MISMATCH for {path}")
        return EXIT_OK if not missing and not different else EXIT_FAIL
    except (RuntimeError, ValueError, OSError, KeyError, tarfile.TarError) as exc:
        say(f"FAIL: {exc}")
        return EXIT_FAIL
    except KeyboardInterrupt:
        say("interrupted")
        return EXIT_FAIL
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
