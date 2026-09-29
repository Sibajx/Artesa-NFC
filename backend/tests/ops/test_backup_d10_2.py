"""D10.2 (#126): off-host copies for artesa-backup (backup_remote.py).

The real artesa_backup + backup_remote code runs against the fake World of
helpers.py (pg_dump etc.), the fake age of test_backup_d10, and either the
directory-backed FakeStore or the real B2Store talking to an in-memory fake
of the B2 native API (FakeB2). No network, no account, no real credential.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import artesa_backup as ab
import backup_remote as br
import release_common as rc
from tests.ops.helpers import NOW
from tests.ops.test_backup_d10 import backups, ctx, layout, plaintext_anywhere, s, state  # noqa: F401 -- fixture reuse

APP_KEY = "K005-super-secret-application-key-value"
KEY_ID = "0051234567890abcdef0000000001"
DEADMAN = "https://hc-ping.example.test/5f1c2d3e-secret-check-uuid"


def remote_env(s, text: str | None = None, *, mode: int = 0o600, **values) -> Path:
    path = layout(s).base / "remote.env"
    if text is None:
        base = {"ARTESA_BACKUP_REMOTE": "fake", "ARTESA_BACKUP_FAKE_REMOTE_DIR": str(s.root.parent / "bucket"),
                "ARTESA_BACKUP_B2_BUCKET": "artesanfc-backups"}
        base.update(values)
        text = "".join(f"{k}={v}\n" for k, v in base.items() if v is not None)
    path.write_text(text)
    os.chmod(path, mode)
    return path


def bucket(s) -> Path:
    return s.root.parent / "bucket"


class Pings:
    def __init__(self, status: int = 200, error: bool = False) -> None:
        self.status, self.error, self.urls = status, error, []

    def __call__(self, method, url, headers, body, timeout):
        self.urls.append(url)
        if self.error:
            raise urllib.error.URLError("no route")
        return self.status, b"OK"


def run(s, *argv, store=None, http=None, **kw):
    c = ctx(s, **kw)
    if store is not None:
        c.remote_store = store
    if http is not None:
        c.http = http
    return ab.main(["--root", str(s.root), "--rehearsal", *argv], c)


def remote_objects(s) -> list[str]:
    if not bucket(s).exists():
        return []
    return sorted(str(p.relative_to(bucket(s))) for p in bucket(s).rglob("*") if p.is_file() and not p.name.endswith(".info"))


def expected_objects(backup_names, when_by_name) -> list[str]:
    """The object names upload_backup must produce: every tier of the day."""
    return sorted(base + f for n in backup_names for _t, base in br.object_bases(br.DEFAULT_PREFIX, n, when_by_name[n])
                  for f in ("bundle.tar.age", "meta.json"))


def created(b: Path) -> datetime:
    return datetime.fromisoformat(json.loads((b / "meta.json").read_text())["created_at"].replace("Z", "+00:00"))


def down(cfg):
    return br.FakeStore(cfg, fail_puts=99)


# --- configuration ------------------------------------------------------------------------------------------------

def test_without_remote_env_nothing_changes(s):
    assert run(s, "run") == 0
    assert "OFFSITE: NOT CONFIGURED -- D10: INCOMPLETE" in s.sink.text
    assert state(s)["offsite"] == {"status": "not-configured"}
    assert not layout(s).offsite_dir.exists()


@pytest.mark.parametrize("text, needle", [
    ("ARTESA_BACKUP_REMOTE=b2\n", "are required"),
    ("ARTESA_BACKUP_REMOTE=s3\n", "must be 'b2'"),
    ("ARTESA_BACKUP_REMOTE=b2\nARTESA_BACKUP_SOMETHING=x\n", "unknown key"),
    (f"ARTESA_BACKUP_REMOTE=b2\nARTESA_BACKUP_B2_KEY_ID={KEY_ID}\nARTESA_BACKUP_B2_APPLICATION_KEY={APP_KEY}\n"
     "ARTESA_BACKUP_B2_BUCKET=artesanfc-backups\nARTESA_BACKUP_B2_PREFIX=/abs/\n", "PREFIX"),
    (f"ARTESA_BACKUP_REMOTE=b2\nARTESA_BACKUP_B2_KEY_ID={KEY_ID}\nARTESA_BACKUP_B2_APPLICATION_KEY={APP_KEY}\n"
     "ARTESA_BACKUP_B2_BUCKET=artesanfc-backups\nARTESA_BACKUP_DEADMAN_URL=http://example.test/x\n", "https"),
    ("ARTESA_BACKUP_REMOTE=b2\n# AGE-SECRET-KEY-1QQQQ\n", "PRIVATE key"),
])
def test_bad_remote_env_is_a_config_error(tmp_path, text, needle):
    path = tmp_path / "remote.env"
    path.write_text(text)
    os.chmod(path, 0o600)
    with pytest.raises(rc.OpsError) as exc:
        br.read_remote_config(path, rehearsal=False)
    assert exc.value.code == rc.Exit.CONFIG and needle in exc.value.message
    assert APP_KEY not in exc.value.message


def test_fake_provider_needs_rehearsal(tmp_path):
    path = tmp_path / "remote.env"
    path.write_text(f"ARTESA_BACKUP_REMOTE=fake\nARTESA_BACKUP_FAKE_REMOTE_DIR={tmp_path}/b\n")
    os.chmod(path, 0o600)
    with pytest.raises(rc.OpsError, match="only accepted with --rehearsal"):
        br.read_remote_config(path, rehearsal=False)
    assert br.read_remote_config(path, rehearsal=True).provider == "fake"


def test_b2_config_reads_and_registers_secrets(tmp_path):
    path = tmp_path / "remote.env"
    path.write_text(f"ARTESA_BACKUP_REMOTE=b2\nARTESA_BACKUP_B2_KEY_ID={KEY_ID}\nARTESA_BACKUP_B2_APPLICATION_KEY={APP_KEY}\n"
                    f"ARTESA_BACKUP_B2_BUCKET=artesanfc-backups\nARTESA_BACKUP_DEADMAN_URL={DEADMAN}\n")
    os.chmod(path, 0o600)
    cfg = br.read_remote_config(path, rehearsal=False)
    assert (cfg.provider, cfg.bucket, cfg.prefix) == ("b2", "artesanfc-backups", br.DEFAULT_PREFIX)
    assert set(cfg.secrets()) == {APP_KEY, DEADMAN} and APP_KEY not in repr(cfg) and DEADMAN not in repr(cfg)


def test_misconfigured_remote_keeps_the_local_backup_and_fails_the_run(s):
    remote_env(s, mode=0o644)
    assert run(s, "run") == rc.Exit.BACKUP
    assert len(backups(s)) == 1 and plaintext_anywhere(s) == []
    st = state(s)
    assert st["last_result"] == "success" and st["offsite"]["status"] == "failed" and "0644" in st["offsite"]["last_error"]["message"]
    meta = json.loads((backups(s)[0] / "meta.json").read_text())
    assert meta["remote"] == {"status": "misconfigured"}
    assert "OFFSITE: FAILED" in s.sink.text


# --- the no-delete model ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("caps, needle", [
    (("listFiles", "writeFiles", "deleteFiles"), "too powerful (deleteFiles)"),
    (("listFiles", "writeFiles", "writeBuckets"), "too powerful (writeBuckets)"),
    (("listFiles", "writeFiles", "bypassGovernance"), "too powerful (bypassGovernance)"),
    (("listFiles", "writeFiles", "writeKeys"), "too powerful (writeKeys)"),
    (("writeFiles",), "lacks listFiles"),
])
def test_a_credential_that_could_delete_or_is_incomplete_is_refused_before_uploading(s, caps, needle):
    remote_env(s)
    assert run(s, "run", store=lambda cfg: br.FakeStore(cfg, capabilities=caps)) == rc.Exit.BACKUP
    assert needle in state(s)["offsite"]["last_error"]["message"] and remote_objects(s) == []
    assert len(backups(s)) == 1


def test_credential_restrictions(tmp_path):
    cfg = br.RemoteConfig("fake", "artesanfc-backups", "artesanfc/prod/postgres/", fake_dir=tmp_path / "b")
    with pytest.raises(rc.OpsError, match="exactly the configured bucket"):
        br._check_access(br.Access(("listFiles", "writeFiles"), "other-bucket", None), cfg)
    with pytest.raises(rc.OpsError, match="does not cover"):
        br._check_access(br.Access(("listFiles", "writeFiles"), "artesanfc-backups", "artesanfc/staging/"), cfg)
    assert br._check_access(br.Access(("listFiles", "writeFiles"), "artesanfc-backups", "artesanfc/"), cfg) == []
    warnings = br._check_access(br.Access(("listFiles", "writeFiles", "readFiles"), "artesanfc-backups", None), cfg)
    assert "not restricted to a name prefix" in warnings[0]


def test_the_module_has_no_delete_or_hide_call():
    source = Path(br.__file__).read_text()
    for call in ("b2_delete_file_version", "b2_hide_file", "b2_delete_bucket", "b2_update_bucket", "DeleteObject"):
        assert call not in source


# --- upload and verification ----------------------------------------------------------------------------------------

def test_run_uploads_and_verifies_the_ciphertext_and_meta(s):
    remote_env(s)
    assert run(s, "run") == 0
    [b] = backups(s)
    assert remote_objects(s) == expected_objects([b.name], {b.name: created(b)})
    for key in remote_objects(s):
        assert (bucket(s) / key).read_bytes() == (b / Path(key).name).read_bytes()
    record = json.loads(layout(s).offsite_record(b.name).read_text())
    assert record["status"] == "verified" and len(record["objects"]) == len(remote_objects(s)) and all(o["uploaded"] for o in record["objects"])
    assert record["objects"][0]["sha256"] == hashlib.sha256((b / "bundle.tar.age").read_bytes()).hexdigest()
    st = state(s)["offsite"]
    assert st["status"] == "verified" and st["last_verified_backup_id"] == b.name and st["consecutive_failures"] == 0
    meta = json.loads((b / "meta.json").read_text())
    assert meta["remote"] == {"status": "offsite-pending", "provider": "fake", "bucket": "artesanfc-backups", "prefix": "artesanfc/prod/postgres/"}
    assert "OFFSITE: VERIFIED (fake, bucket artesanfc-backups)" in s.sink.text
    assert sorted(p.name for p in b.iterdir()) == ["bundle.tar.age", "meta.json"]   # the backup directory is untouched
    assert plaintext_anywhere(s) == [] and not any(p.suffix in (".dump", ".tar") for p in bucket(s).rglob("*"))


@pytest.mark.parametrize("when, tiers", [
    (datetime(2026, 10, 4, 9, 30, tzinfo=timezone.utc), ["daily", "weekly"]),              # Sunday 03:30 in Mexico City
    (datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc), ["daily", "weekly", "monthly"]),   # Sunday, 1 Nov
    (datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc), ["daily", "monthly"]),
    (datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc), ["daily"]),                         # still 30 Sep in Mexico City
])
def test_tiers_follow_the_mexico_city_calendar(when, tiers):
    bases = br.object_bases("p/", "20261004T093000Z-aaaaaaaaaaaa", when)
    assert [t for t, _ in bases] == tiers
    local = when.astimezone(br.SCHEDULE_TZ)
    assert all(base == f"p/{t}/{local:%Y/%m/%d}/20261004T093000Z-aaaaaaaaaaaa/" for t, base in bases)


def test_an_outage_keeps_the_local_backup_and_the_next_run_backfills(s):
    remote_env(s)
    assert run(s, "run", store=down) == rc.Exit.BACKUP
    assert "OFFSITE: FAILED" in s.sink.text and state(s)["offsite"]["consecutive_failures"] == 1
    assert run(s, "run", store=down, clock=lambda: NOW + timedelta(days=1)) == rc.Exit.BACKUP
    assert state(s)["offsite"]["consecutive_failures"] == 2
    assert run(s, "run", clock=lambda: NOW + timedelta(days=2)) == 0
    tool = ab.Tool(ctx(s))
    assert len(backups(s)) == 3 and all(tool._offsite_verified(b.name) for b in backups(s))
    assert remote_objects(s) == expected_objects([b.name for b in backups(s)], {b.name: created(b) for b in backups(s)})
    assert state(s)["offsite"]["consecutive_failures"] == 0


def test_uploads_are_idempotent_by_object_name(s):
    remote_env(s)
    assert run(s, "run") == 0
    [b] = backups(s)
    layout(s).offsite_record(b.name).unlink()      # forget the record: the objects are found, not re-uploaded
    stores = []

    def store(cfg):
        stores.append(br.FakeStore(cfg))
        return stores[-1]
    assert run(s, "run", store=store, clock=lambda: NOW + timedelta(days=1)) == 0
    record = json.loads(layout(s).offsite_record(b.name).read_text())
    newest = backups(s)[-1]
    assert not any(o["uploaded"] for o in record["objects"])
    assert stores[0].puts == len(expected_objects([newest.name], {newest.name: created(newest)}))   # only the new backup's objects


def test_a_different_object_under_the_same_name_is_a_conflict_never_overwritten(s):
    remote_env(s)
    assert run(s, "run") == 0
    [b] = backups(s)
    bundle = sorted(bucket(s).rglob("bundle.tar.age"))[0]
    bundle.write_bytes(b"not the same ciphertext")          # someone else's object under our name
    layout(s).offsite_record(b.name).unlink()
    assert run(s, "run", clock=lambda: NOW + timedelta(days=1)) == rc.Exit.BACKUP
    assert "conflict" in state(s)["offsite"]["last_error"]["message"]
    assert bundle.read_bytes() == b"not the same ciphertext"


def test_a_corrupted_upload_fails_verification(s):
    remote_env(s)
    assert run(s, "run", store=lambda cfg: br.FakeStore(cfg, corrupt=True)) == rc.Exit.BACKUP
    assert "different content" in state(s)["offsite"]["last_error"]["message"]
    assert not layout(s).offsite_dir.exists() or not list(layout(s).offsite_dir.iterdir())


# --- retention with off-host copies ---------------------------------------------------------------------------------------

def test_retention_never_deletes_a_backup_without_a_verified_remote_copy(s):
    remote_env(s)
    for i in range(9):
        run(s, "run", store=down, clock=lambda i=i: NOW + timedelta(days=i))
    assert len(backups(s)) == 9                      # nothing is verified remotely: nothing is deleted
    assert run(s, "run", clock=lambda: NOW + timedelta(days=9)) == 0
    assert len(backups(s)) == 7
    assert len(remote_objects(s)) == len(expected_objects([f"{(NOW + timedelta(days=i)):%Y%m%dT%H%M%SZ}-x" for i in range(10)],
                                                          {f"{(NOW + timedelta(days=i)):%Y%m%dT%H%M%SZ}-x": NOW + timedelta(days=i) for i in range(10)}))
    # all 10 were uploaded (backfill) before retention trimmed the local copies to 7
    assert sorted(p.stem for p in layout(s).offsite_dir.iterdir()) == sorted(b.name for b in backups(s))


def test_retention_is_unchanged_without_remote(s):
    for i in range(9):
        assert run(s, "run", clock=lambda i=i: NOW + timedelta(days=i)) == 0
    assert len(backups(s)) == 7


# --- status ---------------------------------------------------------------------------------------------------------------------

def test_status_reports_verified_offsite_then_stale(s):
    remote_env(s)
    assert run(s, "run") == 0
    assert run(s, "status", clock=lambda: NOW + timedelta(hours=2)) == 0
    text = s.sink.text
    assert "OFFSITE: VERIFIED  fake bucket artesanfc-backups" in text and "D10: INCOMPLETE (recovery drills: D10.3)" in text
    assert run(s, "status", "--json", clock=lambda: NOW + timedelta(hours=2)) == 0
    report = json.loads(s.sink.text)
    assert report["offsite_configured"] and report["offsite_status"] == "verified" and report["offsite_age_hours"] == 2.0
    assert run(s, "status", clock=lambda: NOW + timedelta(hours=27)) == rc.Exit.PREFLIGHT and "OFFSITE STALE" in s.sink.text


def test_status_reports_offsite_failure(s):
    remote_env(s)
    run(s, "run", store=down)
    assert run(s, "status", clock=lambda: NOW + timedelta(hours=1)) == rc.Exit.PREFLIGHT
    assert "OFFSITE: FAILED" in s.sink.text and "offsite failures in a row 1" in s.sink.text


def test_status_configured_but_never_ran_is_pending_and_not_healthy(s):
    assert run(s, "run") == 0
    remote_env(s)
    assert run(s, "status", clock=lambda: NOW + timedelta(hours=1)) == rc.Exit.PREFLIGHT
    assert "OFFSITE: PENDING (CONFIGURED, NO RUN SINCE)" in s.sink.text


# --- dead-man's switch and secrets ------------------------------------------------------------------------------------------------

def test_deadman_is_pinged_only_after_a_fully_successful_run(s):
    remote_env(s, ARTESA_BACKUP_DEADMAN_URL=DEADMAN)
    pings = Pings()
    assert run(s, "run", http=pings) == 0 and pings.urls == [DEADMAN]
    assert state(s)["offsite"]["deadman_last_ping"]["ok"] is True
    pings = Pings()
    assert run(s, "run", http=pings, store=down, clock=lambda: NOW + timedelta(days=1)) == rc.Exit.BACKUP
    assert pings.urls == []


def test_a_failed_ping_only_warns(s):
    remote_env(s, ARTESA_BACKUP_DEADMAN_URL=DEADMAN)
    assert run(s, "run", http=Pings(error=True)) == 0
    assert "dead-man's switch ping failed" in s.sink.text and state(s)["offsite"]["deadman_last_ping"]["ok"] is False
    assert run(s, "status", clock=lambda: NOW + timedelta(hours=1)) == 0 and "last dead-man's switch ping failed" in s.sink.text


def test_secrets_never_reach_output_state_or_log(s):
    remote_env(s, ARTESA_BACKUP_DEADMAN_URL=DEADMAN)

    class Leaky(br.FakeStore):
        def authorize(self):   # an error message that would echo both secrets
            raise br.RemoteError(rc.Exit.BACKUP, f"auth failed for {APP_KEY} at {DEADMAN}")
    c = ctx(s)
    c.guard.add(APP_KEY)
    c.remote_store = Leaky
    assert ab.main(["--root", str(s.root), "--rehearsal", "run"], c) == rc.Exit.BACKUP
    blobs = [s.sink.text, layout(s).status_file.read_text(), layout(s).log_file.read_text()]
    assert all(APP_KEY not in blob and DEADMAN not in blob for blob in blobs)


# --- remote-check -----------------------------------------------------------------------------------------------------------------

def test_remote_check_is_read_only_and_reports_the_credential(s):
    remote_env(s, ARTESA_BACKUP_DEADMAN_URL=DEADMAN)
    pings = Pings()
    assert run(s, "remote-check", "--ping-deadman", http=pings) == 0
    text = s.sink.text
    assert "capabilities: listFiles, writeFiles" in text and "no-delete model  OK" in text and "remote-check PASS (nothing was uploaded)" in text
    assert pings.urls == [DEADMAN] and remote_objects(s) == [] and DEADMAN not in text


def test_remote_check_refuses_a_deleting_key_and_needs_config(s):
    assert run(s, "remote-check") == rc.Exit.CONFIG and "not configured" in s.sink.text
    remote_env(s)
    assert run(s, "remote-check", store=lambda cfg: br.FakeStore(cfg, capabilities=("listFiles", "writeFiles", "deleteFiles"))) == rc.Exit.CONFIG
    assert "too powerful" in s.sink.text


# --- B2Store against an in-memory fake of the B2 native API ------------------------------------------------------------------------

class FakeB2:
    """b2_authorize_account, b2_list_file_names, b2_get_upload_url, upload."""

    def __init__(self, *, capabilities=("listFiles", "writeFiles"), buckets=({"id": "bkt1", "name": "artesanfc-backups"},),
                 name_prefix="artesanfc/prod/postgres/", upload_failures=(), auth_status=200) -> None:
        self.capabilities, self.buckets, self.name_prefix = list(capabilities), list(buckets), name_prefix
        self.upload_failures = list(upload_failures)
        self.auth_status = auth_status
        self.files: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.upload_urls = 0

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers)))
        path = urllib.parse.urlsplit(url).path
        if path.endswith("/b2_authorize_account"):
            assert headers["Authorization"] == "Basic " + base64.b64encode(f"{KEY_ID}:{APP_KEY}".encode()).decode()
            if self.auth_status != 200:
                return self.auth_status, json.dumps({"code": "unauthorized", "message": f"bad key {APP_KEY}"}).encode()
            return 200, json.dumps({"accountId": "acc", "authorizationToken": "ACCOUNT-TOKEN-123456",
                                    "apiInfo": {"storageApi": {"apiUrl": "https://api005.backblazeb2.test", "downloadUrl": "https://f005.test",
                                                               "allowed": {"buckets": self.buckets, "capabilities": self.capabilities,
                                                                           "namePrefix": self.name_prefix}}}}).encode()
        if path.endswith("/b2_list_file_names"):
            assert headers["Authorization"] == "ACCOUNT-TOKEN-123456"
            prefix = json.loads(body)["prefix"]
            files = [dict(f, fileName=n) for n, f in sorted(self.files.items()) if n.startswith(prefix)][:1]
            return 200, json.dumps({"files": files, "nextFileName": None}).encode()
        if path.endswith("/b2_get_upload_url"):
            self.upload_urls += 1
            return 200, json.dumps({"bucketId": "bkt1", "uploadUrl": f"https://pod-000.backblaze.test/upload/{self.upload_urls}",
                                    "authorizationToken": f"UPLOAD-TOKEN-{self.upload_urls:06d}"}).encode()
        if "/upload/" in path:
            if self.upload_failures:
                return self.upload_failures.pop(0), json.dumps({"code": "service_unavailable"}).encode()
            name = urllib.parse.unquote(headers["X-Bz-File-Name"])
            assert int(headers["Content-Length"]) == len(body)
            sha1 = hashlib.sha1(body).hexdigest()
            if sha1 != headers["X-Bz-Content-Sha1"]:
                return 400, json.dumps({"code": "bad_request", "message": "sha1 did not match"}).encode()
            self.files[name] = {"fileId": f"4_z{len(self.files)}", "contentLength": len(body), "contentSha1": sha1,
                                "fileInfo": {"sha256": headers["X-Bz-Info-sha256"]}, "action": "upload"}
            return 200, json.dumps({**self.files[name], "fileName": name}).encode()
        return 404, b"{}"


def b2cfg() -> br.RemoteConfig:
    return br.RemoteConfig("b2", "artesanfc-backups", "artesanfc/prod/postgres/", KEY_ID, APP_KEY)


def test_b2_upload_and_verify_end_to_end(tmp_path):
    fake = FakeB2()
    store = br.B2Store(b2cfg(), transport=fake)
    backup = tmp_path / "20261004T093000Z-aaaaaaaaaaaa"
    backup.mkdir()
    (backup / "bundle.tar.age").write_bytes(b"age-encryption.org/v1\n" + os.urandom(64))
    (backup / "meta.json").write_text("{}")
    when = datetime(2026, 10, 4, 9, 30, tzinfo=timezone.utc)
    record = br.upload_backup(store, backup, backup.name, when, ("bundle.tar.age", "meta.json"), lambda: "2026-10-04T09:31:00Z")
    assert len(record["objects"]) == 4 and {o["tier"] for o in record["objects"]} == {"daily", "weekly"}
    assert sorted(fake.files) == sorted(f"artesanfc/prod/postgres/{t}/2026/10/04/{backup.name}/{n}"
                                        for t in ("daily", "weekly") for n in ("bundle.tar.age", "meta.json"))
    uploads = [h for _m, u, h in fake.calls if "/upload/" in u]
    assert uploads[0]["Content-Type"] == "application/age-encryption" and uploads[0]["X-Bz-Info-sha256"] == record["objects"][0]["sha256"]
    assert fake.upload_urls == 1   # the upload URL is reused
    again = br.upload_backup(store, backup, backup.name, when, ("bundle.tar.age", "meta.json"), lambda: "x")
    assert not any(o["uploaded"] for o in again["objects"]) and len([1 for _m, u, _h in fake.calls if "/upload/" in u]) == 4


def test_b2_retries_503_and_401_with_a_new_upload_url_and_backoff(tmp_path):
    fake, sleeps = FakeB2(upload_failures=[503, 401]), []
    store = br.B2Store(b2cfg(), transport=fake, sleep=sleeps.append)
    f = tmp_path / "x.bin"
    f.write_bytes(b"payload")
    obj = store.put("artesanfc/prod/postgres/daily/x.bin", f, hashlib.sha1(b"payload").hexdigest(), "s" * 64, "application/octet-stream")
    assert obj.size == 7 and fake.upload_urls == 3 and sleeps == list(br.BACKOFF)


def test_b2_gives_up_after_three_attempts_and_does_not_retry_a_400(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"payload")
    store = br.B2Store(b2cfg(), transport=FakeB2(upload_failures=[503, 503, 503]))
    with pytest.raises(br.RemoteError, match="HTTP 503"):
        store.put("artesanfc/prod/postgres/daily/x.bin", f, hashlib.sha1(b"payload").hexdigest(), "s" * 64, "application/octet-stream")
    fake = FakeB2()
    store = br.B2Store(b2cfg(), transport=fake)
    with pytest.raises(br.RemoteError, match="HTTP 400"):
        store.put("artesanfc/prod/postgres/daily/x.bin", f, "0" * 40, "s" * 64, "application/octet-stream")
    assert fake.upload_urls == 1


@pytest.mark.parametrize("kw, needle", [
    ({"capabilities": ("listFiles", "writeFiles", "deleteFiles")}, "too powerful"),
    ({"buckets": []}, "exactly the configured bucket"),                       # an unrestricted key
    ({"buckets": [{"id": "b2", "name": "someone-else"}]}, "exactly the configured bucket"),
    ({"name_prefix": "other/"}, "does not cover"),
])
def test_b2_authorize_enforces_the_no_delete_model(kw, needle):
    with pytest.raises(rc.OpsError, match=needle):
        br.B2Store(b2cfg(), transport=FakeB2(**kw)).authorize()


def test_b2_errors_never_echo_the_key():
    with pytest.raises(br.RemoteError) as exc:
        br.B2Store(b2cfg(), transport=FakeB2(auth_status=401)).authorize()
    assert "HTTP 401" in exc.value.message and APP_KEY not in exc.value.message

    def unreachable(*_a):
        raise urllib.error.URLError(OSError("certificate verify failed"))
    with pytest.raises(br.RemoteError, match="cannot reach the remote"):
        br.B2Store(b2cfg(), transport=unreachable).authorize()


def test_b2_refuses_plain_http():
    with pytest.raises(br.RemoteError, match="non-https"):
        br.B2Store(b2cfg(), transport=FakeB2(), authorize_url="http://api.backblazeb2.com/b2api/v4/b2_authorize_account").authorize()


def test_b2_stat_ignores_hidden_versions():
    fake = FakeB2()
    fake.files["artesanfc/prod/postgres/daily/a"] = {"fileId": "1", "contentLength": 1, "contentSha1": "x", "fileInfo": {}, "action": "hide"}
    assert br.B2Store(b2cfg(), transport=fake).stat("artesanfc/prod/postgres/daily/a") is None


def test_run_with_the_real_b2_client_against_the_fake_api(s):
    remote_env(s, text=f"ARTESA_BACKUP_REMOTE=b2\nARTESA_BACKUP_B2_KEY_ID={KEY_ID}\nARTESA_BACKUP_B2_APPLICATION_KEY={APP_KEY}\n"
                       "ARTESA_BACKUP_B2_BUCKET=artesanfc-backups\n")
    fake = FakeB2()
    assert run(s, "run", store=lambda cfg: br.B2Store(cfg, transport=fake)) == 0
    [b] = backups(s)
    assert sorted(fake.files) == expected_objects([b.name], {b.name: created(b)})
    assert APP_KEY not in s.sink.text and APP_KEY not in layout(s).log_file.read_text()


def test_remote_check_detects_a_hidden_or_changed_remote_copy(s):
    remote_env(s)
    assert run(s, "run") == 0
    assert run(s, "remote-check") == 0 and "verified objects still present and identical" in s.sink.text
    victim = sorted(bucket(s).rglob("bundle.tar.age"))[0]
    victim.unlink()                                   # what a "hide" looks like from the listing
    assert run(s, "remote-check") == rc.Exit.BACKUP
    assert f"MISSING or CHANGED: {victim.relative_to(bucket(s))}" in s.sink.text and "remote-check FAIL" in s.sink.text
