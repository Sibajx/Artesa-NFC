"""docs/DEPLOYMENT.md §11.8: `artesa-deploy fetch` (GitHub Releases) and `artesa-deploy ui`."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

import release_common as rc
import release_fetch as rf
from tests.ops.helpers import Scenario, build, commit

def ui_tarball(files: dict[str, bytes], *, extra: list[tarfile.TarInfo] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.addfile(tarfile.TarInfo("./"))  # like `tar -C dist -czf x.tgz .`
        for name, data in files.items():
            info = tarfile.TarInfo(f"./{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isreg() else None)
    return buffer.getvalue()


def sidecar(name: str, data: bytes) -> bytes:
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


class FakeGitHub:
    """Answers the releases API and asset URLs from an in-memory release."""

    def __init__(self, release_id: str, files: dict[str, bytes]) -> None:
        self.release_id, self.files, self.calls = release_id, dict(files), []

    def doc(self) -> bytes:
        assets = [{"name": n, "url": f"https://api.github.com/repos/{rf.REPOSITORY}/releases/assets/{i}"}
                  for i, n in enumerate(self.files)]
        return json.dumps({"tag_name": f"release-{self.release_id}", "assets": assets}).encode()

    def __call__(self, url: str, limit: int) -> bytes:
        self.calls.append(url)
        if url in (f"{rf.API}/latest", f"{rf.API}/tags/release-{self.release_id}"):
            return self.doc()
        if "/releases/assets/" in url:
            name = list(self.files)[int(url.rsplit("/", 1)[1])]
            return self.files[name]
        raise rf.FetchError(f"GitHub answered HTTP 404 for {url}")


@pytest.fixture
def s(tmp_path):
    return Scenario(tmp_path)


def published(s: Scenario, tmp_path: Path) -> tuple[str, str, FakeGitHub]:
    """A real artifact built from the repo, published with a UI tarball."""
    commit(s.repo, {"backend/app/fetched.py": "X = 1\n"}, "fetched release")
    out = tmp_path / "dist"
    result = build(s.repo, out, ref="HEAD", rehearsal=True)
    rid = result.artifact.name.removeprefix("artesa-nfc-").removesuffix(".tar.gz")
    commit12 = rid.rsplit("-", 1)[1]
    artifact = result.artifact.read_bytes()
    ui = ui_tarball({"index.html": b"<html>gestion</html>", "assets/app.js": b"console.log(1)"})
    files = {
        result.artifact.name: artifact,
        f"{result.artifact.name}.sha256": (out / f"{result.artifact.name}.sha256").read_bytes(),
        f"admin-ui-{commit12}.tgz": ui,
        f"admin-ui-{commit12}.tgz.sha256": sidecar(f"admin-ui-{commit12}.tgz", ui),
    }
    return rid, commit12, FakeGitHub(rid, files)


def run(s: Scenario, argv: list[str], github: FakeGitHub | None = None, **kw) -> int:
    ctx = s.ctx(**kw)
    if github is not None:
        ctx.download = github
    import artesa_deploy as ad
    return ad.main(["--root", str(s.root), "--rehearsal", *argv], ctx)


# --- fetch -------------------------------------------------------------------------------------------


def test_fetch_latest_puts_verified_files_in_incoming_and_prints_the_next_steps(s, tmp_path):
    rid, commit12, github = published(s, tmp_path)
    assert run(s, ["fetch"], github) == 0
    incoming = s.root / "incoming"
    assert (incoming / rc.artifact_filename(rid)).is_file()
    assert (incoming / f"admin-ui-{commit12}.tgz").is_file()
    text = s.sink.text
    assert f"bin/artesa-deploy prepare {rid}" in text
    assert f"bin/artesa-deploy deploy --expect-commit {commit12} {rid}" in text
    assert f"bin/artesa-deploy ui {commit12}" in text
    assert github.calls[0] == f"{rf.API}/latest"
    event = [e for e in s.log_events() if e["event"] == "fetch"][-1]
    assert event["target_release"] == rid and event["git_sha"] == commit12
    # prepare's artifact gate accepts what fetch downloaded (the interpreter gate
    # depends on the runner's Python, not on fetch, so it is not asserted here)
    run(s, ["prepare", rid, "--dry-run"])
    assert "[PASS] artifact + sidecar + manifest + RELEASE.json" in s.sink.text, s.sink.text


def test_fetch_a_named_release_and_running_it_again_is_harmless(s, tmp_path):
    rid, _, github = published(s, tmp_path)
    assert run(s, ["fetch", rid], github) == 0
    assert github.calls[0] == f"{rf.API}/tags/release-{rid}"
    assert run(s, ["fetch", rid], github) == 0
    assert "already in incoming/" in s.sink.text


def test_a_tampered_asset_is_refused_and_nothing_is_kept(s, tmp_path):
    rid, commit12, github = published(s, tmp_path)
    name = rc.artifact_filename(rid)
    github.files[name] = github.files[name][:-1] + b"!"
    assert run(s, ["fetch"], github) == int(rc.Exit.ARTIFACT_INVALID)
    assert "does not match its published SHA-256" in s.sink.text
    assert not any((s.root / "incoming").glob("*" + commit12 + "*"))


def test_a_different_existing_file_is_never_overwritten(s, tmp_path):
    rid, _, github = published(s, tmp_path)
    (s.root / "incoming" / rc.artifact_filename(rid)).write_bytes(b"something else")
    assert run(s, ["fetch"], github) == int(rc.Exit.ARTIFACT_INVALID)
    assert "different content" in s.sink.text
    assert (s.root / "incoming" / rc.artifact_filename(rid)).read_bytes() == b"something else"


def test_an_unreachable_github_points_to_scp(s):
    def offline(url, limit):
        raise rf.FetchError("cannot reach GitHub (SSLCertVerificationError: x); copy the files with scp instead (docs/DEPLOYMENT.md §11.8)")

    assert run(s, ["fetch"], offline) == int(rc.Exit.ARTIFACT_INVALID)  # type: ignore[arg-type]
    assert "scp" in s.sink.text


def test_a_release_with_a_foreign_tag_is_refused(s, tmp_path):
    rid, _, github = published(s, tmp_path)
    github.doc = lambda: json.dumps({"tag_name": "v1.0", "assets": []}).encode()  # type: ignore[method-assign]
    assert run(s, ["fetch"], github) == int(rc.Exit.ARTIFACT_INVALID)


def test_https_download_refuses_plain_http():
    with pytest.raises(rf.FetchError, match="https"):
        rf.https_download("http://example.org/x", 10)


# --- ui ----------------------------------------------------------------------------------------------


def stage_ui(s: Scenario, commit12: str, tarball: bytes) -> None:
    incoming = s.root / "incoming"
    (incoming / f"admin-ui-{commit12}.tgz").write_bytes(tarball)
    (incoming / f"admin-ui-{commit12}.tgz.sha256").write_bytes(sidecar(f"admin-ui-{commit12}.tgz", tarball))


def test_ui_installs_switches_atomically_and_rolls_back(s):
    old, new = "aaaaaaaaaaaa", "bbbbbbbbbbbb"
    stage_ui(s, old, ui_tarball({"index.html": b"old"}))
    stage_ui(s, new, ui_tarball({"index.html": b"new", "assets/a.js": b"1"}))
    ui_root = s.root / "shared" / "admin-ui"
    assert run(s, ["ui", old]) == 0
    assert os.readlink(ui_root / "current") == old
    assert run(s, ["ui", new]) == 0
    assert os.readlink(ui_root / "current") == new and (ui_root / "current" / "assets" / "a.js").read_bytes() == b"1"
    assert f"previous {old}" in s.sink.text and f"rollback: bin/artesa-deploy ui {old}" in s.sink.text
    assert oct((ui_root / new / "index.html").stat().st_mode & 0o777) == "0o644"
    # rollback reuses the installed directory
    (s.root / "incoming" / f"admin-ui-{old}.tgz").unlink()
    assert run(s, ["ui", old]) == 0 and "already installed (reused)" in s.sink.text
    assert os.readlink(ui_root / "current") == old
    assert run(s, ["ui"]) == 0 and f"Gestión UI: current {old}" in s.sink.text
    assert [e["detail"] for e in s.log_events() if e["event"] == "ui_activate"][-1].startswith(f"previous={new}")


@pytest.mark.parametrize("bad", ["link", "dotdot", "absolute", "hidden"])
def test_ui_refuses_hostile_tarballs_and_leaves_current_alone(s, bad):
    good, evil = "aaaaaaaaaaaa", "cccccccccccc"
    stage_ui(s, good, ui_tarball({"index.html": b"ok"}))
    assert run(s, ["ui", good]) == 0
    info = tarfile.TarInfo({"dotdot": "../escape.txt", "absolute": "/tmp/escape.txt", "hidden": ".env",
                            "link": "assets/link"}[bad])
    if bad == "link":
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
    else:
        info.size = 1
    stage_ui(s, evil, ui_tarball({"index.html": b"x"}, extra=[info]))
    assert run(s, ["ui", evil]) == int(rc.Exit.ARTIFACT_INVALID)
    ui_root = s.root / "shared" / "admin-ui"
    assert os.readlink(ui_root / "current") == good
    assert not (ui_root / evil).exists() and not list(ui_root.glob(".*tmp*"))


def test_ui_checks_the_sidecar_and_index(s):
    commit12 = "dddddddddddd"
    stage_ui(s, commit12, ui_tarball({"index.html": b"x"}))
    (s.root / "incoming" / f"admin-ui-{commit12}.tgz").write_bytes(ui_tarball({"index.html": b"tampered"}))
    assert run(s, ["ui", commit12]) == int(rc.Exit.ARTIFACT_INVALID)
    other = "eeeeeeeeeeee"
    stage_ui(s, other, ui_tarball({"main.js": b"x"}))
    assert run(s, ["ui", other]) == int(rc.Exit.ARTIFACT_INVALID) and "no index.html" in s.sink.text
    assert run(s, ["ui", "not-a-commit"]) == int(rc.Exit.USAGE)
    assert run(s, ["ui", "ffffffffffff"]) == int(rc.Exit.ARTIFACT_INVALID) and "run `artesa-deploy fetch` first" in s.sink.text


def test_ui_changes_state_only_from_a_terminal(s):
    stage_ui(s, "aaaaaaaaaaaa", ui_tarball({"index.html": b"x"}))
    assert run(s, ["ui", "aaaaaaaaaaaa"], tty=False) == int(rc.Exit.NO_TTY_OR_ABORT)
