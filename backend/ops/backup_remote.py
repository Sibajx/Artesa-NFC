"""Off-host copies for artesa-backup (#126 / D10, phase D10.2).

Standard library only (``urllib`` + ``ssl`` with the system trust store). The
server uploads what ``artesa-backup run`` already produced -- the age
ciphertext and its public ``meta.json`` -- and never anything in clear.

Contract (docs/BACKUP.md §14, ADR in the Brain):
  * **no-delete credential**: the application key on the server may write and
    list; a key that could delete files, hide-and-expire them through bucket
    or key changes, or weaken Object Lock is REFUSED before anything is sent;
  * the key must be restricted to exactly the configured bucket; a name-prefix
    restriction, if present, must cover the configured prefix;
  * remote retention is the provider's lifecycle + Object Lock, set by an
    administrator with MFA; this module never deletes, hides or overwrites;
  * uploads are idempotent per object name (``<prefix><tier>/YYYY/MM/DD/<backup_id>/``):
    an object already present with the same size and SHA-1 counts as done, a
    different one is a conflict (never overwritten);
  * every upload is verified by listing it back: size, SHA-1 and the SHA-256
    recorded in the file info must equal the local file;
  * the remote configuration lives in ``shared/backup/remote.env`` (regular
    file, 0600, owned by the service user); its secret values are scrubbed
    from every output. Without that file off-host copies are NOT CONFIGURED
    and D10.1 behaves exactly as before.

Nothing here reads a private age key or the database.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import ssl
import stat
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import release_common as rc  # noqa: E402  (ops/ is on sys.path; see artesa_backup)
import release_probe as rp  # noqa: E402

REMOTE_ENV_NAME = "remote.env"
B2_AUTHORIZE_URL = "https://api.backblazeb2.com/b2api/v4/b2_authorize_account"
B2_API = "/b2api/v4"
DEFAULT_PREFIX = "artesanfc/prod/postgres/"
SCHEDULE_TZ = ZoneInfo("America/Mexico_City")   # tiers follow the operator's calendar (timer at 03:30 local)
HTTP_TIMEOUT = 60.0
ATTEMPTS = 3
BACKOFF = (2.0, 8.0)
# Capabilities that could delete, hide-and-expire (via bucket/lifecycle changes) or
# weaken immutability. writeFiles can "hide" a file, which never deletes a version;
# Object Lock + lifecycle keep hidden versions for the whole retention (ADR).
FORBIDDEN_CAPABILITIES = frozenset({
    "deleteFiles", "deleteBuckets", "writeBuckets", "writeBucketRetentions", "writeBucketEncryption",
    "writeBucketReplications", "writeBucketNotifications", "deleteKeys", "writeKeys", "bypassGovernance",
    "writeFileRetentions", "writeFileLegalHolds",
})
REQUIRED_CAPABILITIES = frozenset({"writeFiles", "listFiles"})
_KEYS = ("ARTESA_BACKUP_REMOTE", "ARTESA_BACKUP_B2_KEY_ID", "ARTESA_BACKUP_B2_APPLICATION_KEY", "ARTESA_BACKUP_B2_BUCKET",
         "ARTESA_BACKUP_B2_PREFIX", "ARTESA_BACKUP_DEADMAN_URL", "ARTESA_BACKUP_FAKE_REMOTE_DIR")
_PREFIX_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_/.")
_BUCKET_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")


# --- configuration -------------------------------------------------------------------------------

@dataclass(frozen=True)
class RemoteConfig:
    provider: str                      # "b2" | "fake" (rehearsal/tests only)
    bucket: str
    prefix: str
    key_id: str = ""
    application_key: str = field(default="", repr=False)
    deadman_url: str | None = field(default=None, repr=False)
    fake_dir: Path | None = None

    def secrets(self) -> list[str]:
        return [v for v in (self.application_key, self.deadman_url) if v]


def read_remote_config(path: Path, *, rehearsal: bool) -> RemoteConfig | None:
    """None when the file does not exist (off-host copies NOT CONFIGURED).
    Same file rules as backup.env; any problem is a CONFIG error."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise rc.OpsError(rc.Exit.CONFIG, "shared/backup/remote.env must be a regular file, not a symlink")
    if st.st_mode & 0o077:
        raise rc.OpsError(rc.Exit.CONFIG, f"shared/backup/remote.env mode is {stat.S_IMODE(st.st_mode):04o}; it must be 0600")
    if st.st_uid != os.geteuid():
        raise rc.OpsError(rc.Exit.CONFIG, "shared/backup/remote.env must be owned by the service user")
    text = path.read_text(encoding="utf-8", errors="replace")
    if "AGE-SECRET-KEY-" in text.upper():
        raise rc.OpsError(rc.Exit.CONFIG, "shared/backup/remote.env contains an age PRIVATE key; private keys must never be on this host")
    values = rp.parse_env_text(text)
    unknown = sorted(k for k in values if k not in _KEYS)
    if unknown:
        raise rc.OpsError(rc.Exit.CONFIG, f"shared/backup/remote.env has unknown key(s): {', '.join(unknown)}")
    provider = values.get("ARTESA_BACKUP_REMOTE", "").strip()
    prefix = values.get("ARTESA_BACKUP_B2_PREFIX", DEFAULT_PREFIX).strip()
    if not prefix.endswith("/") or prefix.startswith("/") or "//" in prefix or ".." in prefix or not set(prefix) <= _PREFIX_CHARS:
        raise rc.OpsError(rc.Exit.CONFIG, "ARTESA_BACKUP_B2_PREFIX must be a relative lowercase path ending in '/' (e.g. artesanfc/prod/postgres/)")
    deadman = values.get("ARTESA_BACKUP_DEADMAN_URL", "").strip() or None
    if deadman is not None:
        parsed = urllib.parse.urlsplit(deadman)
        if parsed.scheme != "https" and not (rehearsal and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"):
            raise rc.OpsError(rc.Exit.CONFIG, "ARTESA_BACKUP_DEADMAN_URL must be an https URL")
    if provider == "b2":
        key_id = values.get("ARTESA_BACKUP_B2_KEY_ID", "").strip()
        app_key = values.get("ARTESA_BACKUP_B2_APPLICATION_KEY", "").strip()
        bucket = values.get("ARTESA_BACKUP_B2_BUCKET", "").strip()
        if not key_id or not app_key or not bucket:
            raise rc.OpsError(rc.Exit.CONFIG, "remote.env: ARTESA_BACKUP_B2_KEY_ID, ARTESA_BACKUP_B2_APPLICATION_KEY and ARTESA_BACKUP_B2_BUCKET are required")
        if not 6 <= len(bucket) <= 63 or not set(bucket) <= _BUCKET_CHARS:
            raise rc.OpsError(rc.Exit.CONFIG, "ARTESA_BACKUP_B2_BUCKET is not a valid B2 bucket name")
        return RemoteConfig("b2", bucket, prefix, key_id, app_key, deadman)
    if provider == "fake":
        if not rehearsal:
            raise rc.OpsError(rc.Exit.CONFIG, "ARTESA_BACKUP_REMOTE=fake is only accepted with --rehearsal")
        fake_dir = values.get("ARTESA_BACKUP_FAKE_REMOTE_DIR", "").strip()
        if not fake_dir or not os.path.isabs(fake_dir):
            raise rc.OpsError(rc.Exit.CONFIG, "ARTESA_BACKUP_FAKE_REMOTE_DIR must be an absolute path")
        return RemoteConfig("fake", values.get("ARTESA_BACKUP_B2_BUCKET", "fake-bucket").strip() or "fake-bucket", prefix,
                            deadman_url=deadman, fake_dir=Path(fake_dir))
    raise rc.OpsError(rc.Exit.CONFIG, "ARTESA_BACKUP_REMOTE must be 'b2'")


def object_bases(prefix: str, backup_id: str, created_at: datetime) -> list[tuple[str, str]]:
    """(tier, directory) pairs for one backup. daily always; weekly on Sunday;
    monthly on the 1st -- in America/Mexico_City, the timer's calendar. The
    lifecycle rule of each tier prefix does the expiry (35 / 91 / 400 d)."""
    local: date = created_at.astimezone(SCHEDULE_TZ).date()
    tiers = ["daily"] + (["weekly"] if local.weekday() == 6 else []) + (["monthly"] if local.day == 1 else [])
    return [(tier, f"{prefix}{tier}/{local:%Y/%m/%d}/{backup_id}/") for tier in tiers]


# --- objects and stores --------------------------------------------------------------------------

@dataclass(frozen=True)
class RemoteObject:
    name: str
    size: int
    sha1: str
    sha256: str | None
    file_id: str | None = None


@dataclass(frozen=True)
class Access:
    """What the credential may do (from the provider, never assumed)."""
    capabilities: tuple[str, ...]
    bucket: str
    name_prefix: str | None


class RemoteError(rc.OpsError):
    """An off-host step failed. The local encrypted backup is unaffected."""


def _check_access(access: Access, cfg: RemoteConfig) -> list[str]:
    """The no-delete model, enforced before any upload. Returns warnings."""
    caps = set(access.capabilities)
    forbidden = sorted(caps & FORBIDDEN_CAPABILITIES)
    if forbidden:
        raise rc.OpsError(rc.Exit.CONFIG, f"the remote credential is too powerful ({', '.join(forbidden)}); "
                                          "create a key with writeFiles + listFiles only (no delete), restricted to the bucket")
    missing = sorted(REQUIRED_CAPABILITIES - caps)
    if missing:
        raise rc.OpsError(rc.Exit.CONFIG, f"the remote credential lacks {', '.join(missing)}")
    if access.bucket != cfg.bucket:
        raise rc.OpsError(rc.Exit.CONFIG, "the remote credential must be restricted to exactly the configured bucket")
    warnings = []
    if access.name_prefix is None:
        warnings.append("the credential is not restricted to a name prefix (recommended: the configured prefix)")
    elif not cfg.prefix.startswith(access.name_prefix):
        raise rc.OpsError(rc.Exit.CONFIG, "the credential's name prefix does not cover the configured prefix")
    return warnings


Transport = Callable[[str, str, dict, bytes | None, float], tuple[int, bytes]]


def https_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    """One HTTPS request with full certificate verification (system trust
    store). A TLS-inspecting proxy with an untrusted CA fails here, closed."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310 -- https only (checked by callers)
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""


class B2Store:
    """Backblaze B2 native API v4: authorize, list_file_names, get_upload_url,
    upload_file. No delete/hide call exists in this class on purpose."""

    def __init__(self, cfg: RemoteConfig, *, transport: Transport = https_transport, sleep: Callable[[float], None] | None = None,
                 authorize_url: str = B2_AUTHORIZE_URL) -> None:
        self.cfg, self.transport, self.authorize_url = cfg, transport, authorize_url
        self.sleep = sleep or (lambda _s: None)
        self._api_url: str | None = None
        self._token: str | None = None
        self._bucket_id: str | None = None
        self._upload: tuple[str, str] | None = None
        self.warnings: list[str] = []

    # -- plumbing
    def _call(self, method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict]:
        if not url.startswith("https://"):
            raise RemoteError(rc.Exit.BACKUP, "refusing a non-https remote URL")
        try:
            status, raw = self.transport(method, url, headers, body, HTTP_TIMEOUT)
        except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RemoteError(rc.Exit.BACKUP, f"cannot reach the remote ({type(reason).__name__}: {str(reason)[:120]})") from None
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            data = {}
        return status, data if isinstance(data, dict) else {}

    @staticmethod
    def _error(what: str, status: int, data: dict) -> RemoteError:
        return RemoteError(rc.Exit.BACKUP, f"{what} failed: HTTP {status} {str(data.get('code') or '')[:40]}".rstrip())

    def authorize(self) -> Access:
        basic = base64.b64encode(f"{self.cfg.key_id}:{self.cfg.application_key}".encode()).decode()
        status, data = self._call("GET", self.authorize_url, {"Authorization": f"Basic {basic}"}, None)
        if status != 200:
            raise self._error("b2_authorize_account", status, data)
        try:
            storage = data["apiInfo"]["storageApi"]
            allowed = storage["allowed"]
            self._api_url, self._token = storage["apiUrl"], data["authorizationToken"]
            buckets = allowed.get("buckets") or []
            capabilities = tuple(allowed.get("capabilities") or ())
        except (KeyError, TypeError):
            raise RemoteError(rc.Exit.BACKUP, "b2_authorize_account returned an unexpected document") from None
        if len(buckets) != 1 or not isinstance(buckets[0], dict):
            access = Access(capabilities, "", allowed.get("namePrefix"))
        else:
            self._bucket_id = buckets[0].get("id")
            access = Access(capabilities, buckets[0].get("name") or "", allowed.get("namePrefix"))
        self.warnings = _check_access(access, self.cfg)
        return access

    def _ensure(self) -> None:
        if self._token is None:
            self.authorize()

    def stat(self, name: str) -> RemoteObject | None:
        self._ensure()
        body = json.dumps({"bucketId": self._bucket_id, "prefix": name, "maxFileCount": 1}).encode()
        status, data = self._retrying("b2_list_file_names", lambda: self._call(
            "POST", f"{self._api_url}{B2_API}/b2_list_file_names", {"Authorization": self._token, "Content-Type": "application/json"}, body))
        for item in data.get("files") or []:
            if item.get("fileName") == name and item.get("action") == "upload":
                info = item.get("fileInfo") or {}
                return RemoteObject(name, int(item.get("contentLength") or 0), str(item.get("contentSha1") or ""), info.get("sha256"), item.get("fileId"))
        return None

    def put(self, name: str, path: Path, sha1: str, sha256: str, content_type: str) -> RemoteObject:
        self._ensure()
        payload = path.read_bytes()
        last: RemoteError | None = None
        for attempt in range(ATTEMPTS):
            if self._upload is None:
                status, data = self._call("POST", f"{self._api_url}{B2_API}/b2_get_upload_url",
                                          {"Authorization": self._token, "Content-Type": "application/json"},
                                          json.dumps({"bucketId": self._bucket_id}).encode())
                if status != 200:
                    last = self._error("b2_get_upload_url", status, data)
                    if not _retryable(status):
                        raise last
                    self._pause(attempt)
                    continue
                self._upload = (data["uploadUrl"], data["authorizationToken"])
            url, token = self._upload
            headers = {"Authorization": token, "X-Bz-File-Name": urllib.parse.quote(name, safe="/"), "Content-Type": content_type,
                       "Content-Length": str(len(payload)), "X-Bz-Content-Sha1": sha1, "X-Bz-Info-sha256": sha256}
            try:
                status, data = self._call("POST", url, headers, payload)
            except RemoteError as exc:
                last, self._upload = exc, None
                self._pause(attempt)
                continue
            if status == 200:
                if int(data.get("contentLength") or -1) != len(payload) or data.get("contentSha1") != sha1:
                    raise RemoteError(rc.Exit.BACKUP, f"the remote stored {name} with a different size or SHA-1")
                return RemoteObject(name, len(payload), sha1, (data.get("fileInfo") or {}).get("sha256"), data.get("fileId"))
            last = self._error("b2_upload_file", status, data)
            if not _retryable(status):
                raise last
            self._upload = None   # 401/503/408/429/5xx: new upload URL, as B2 documents
            self._pause(attempt)
        raise last or RemoteError(rc.Exit.BACKUP, "upload failed")

    def _retrying(self, what: str, call: Callable[[], tuple[int, dict]]) -> tuple[int, dict]:
        last: RemoteError | None = None
        for attempt in range(ATTEMPTS):
            try:
                status, data = call()
            except RemoteError as exc:
                last = exc
                self._pause(attempt)
                continue
            if status == 200:
                return status, data
            last = self._error(what, status, data)
            if not _retryable(status):
                raise last
            self._pause(attempt)
        raise last or RemoteError(rc.Exit.BACKUP, f"{what} failed")

    def _pause(self, attempt: int) -> None:
        if attempt < len(BACKOFF):
            self.sleep(BACKOFF[attempt])


def _retryable(status: int) -> bool:
    return status in (401, 408, 429) or status >= 500


class FakeStore:
    """A directory standing in for the bucket (rehearsal and tests). It keeps
    the same rules as the real one: write-once names, no delete, verified by
    reading back. ``capabilities`` lets tests present a too-powerful key."""

    def __init__(self, cfg: RemoteConfig, capabilities: tuple[str, ...] = ("listFiles", "writeFiles"), name_prefix: str | None = None,
                 fail_puts: int = 0, corrupt: bool = False) -> None:
        assert cfg.fake_dir is not None
        self.cfg, self.base = cfg, cfg.fake_dir
        self.capabilities, self.name_prefix = capabilities, name_prefix if name_prefix is not None else cfg.prefix
        self.fail_puts, self.corrupt = fail_puts, corrupt
        self.warnings: list[str] = []
        self.puts = 0

    def authorize(self) -> Access:
        access = Access(self.capabilities, self.cfg.bucket, self.name_prefix)
        self.warnings = _check_access(access, self.cfg)
        self.base.mkdir(parents=True, exist_ok=True)
        return access

    def _path(self, name: str) -> Path:
        if name.startswith("/") or ".." in name.split("/"):
            raise RemoteError(rc.Exit.BACKUP, "unsafe object name")
        return self.base / name

    def stat(self, name: str) -> RemoteObject | None:
        path = self._path(name)
        if not path.is_file():
            return None
        data = path.read_bytes()
        info = json.loads(path.with_name(path.name + ".info").read_text()) if path.with_name(path.name + ".info").is_file() else {}
        return RemoteObject(name, len(data), hashlib.sha1(data).hexdigest(), info.get("sha256"), f"fake-{name}")  # noqa: S324 -- B2 uses SHA-1

    def put(self, name: str, path: Path, sha1: str, sha256: str, content_type: str) -> RemoteObject:
        if self.fail_puts > 0:
            self.fail_puts -= 1
            raise RemoteError(rc.Exit.BACKUP, "cannot reach the remote (fake outage)")
        target = self._path(name)
        if target.exists():
            raise RemoteError(rc.Exit.BACKUP, f"{name} already exists (write-once)")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if self.corrupt:
            with open(target, "ab") as handle:
                handle.write(b"x")
        target.with_name(target.name + ".info").write_text(json.dumps({"sha256": sha256, "content_type": content_type}))
        self.puts += 1
        return self.stat(name)  # type: ignore[return-value]


def make_store(cfg: RemoteConfig, **kwargs):
    return FakeStore(cfg) if cfg.provider == "fake" else B2Store(cfg, **kwargs)


# --- upload of one backup ------------------------------------------------------------------------

def _digests(path: Path) -> tuple[int, str, str]:
    sha1, sha256 = hashlib.sha1(), hashlib.sha256()  # noqa: S324 -- SHA-1 is what B2 verifies; SHA-256 is ours
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha1.update(chunk)
            sha256.update(chunk)
    return os.stat(path).st_size, sha1.hexdigest(), sha256.hexdigest()


def upload_backup(store, backup_dir: Path, backup_id: str, created_at: datetime, files: tuple[str, ...], now: Callable[[], str]) -> dict:
    """Upload (or find already uploaded) every file of one backup under every
    tier, then verify each by listing it back. Returns the offsite record.
    Raises RemoteError on any failure or conflict; never deletes anything."""
    objects = []
    for tier, base in object_bases(store.cfg.prefix, backup_id, created_at):
        for name in files:
            local = backup_dir / name
            size, sha1, sha256 = _digests(local)
            key = base + name
            existing = store.stat(key)
            if existing is None:
                store.put(key, local, sha1, sha256, "application/age-encryption" if name.endswith(".age") else "application/json")
                existing = store.stat(key)
                uploaded = True
            else:
                uploaded = False
            if existing is None:
                raise RemoteError(rc.Exit.BACKUP, f"{key} is not listed after upload")
            if existing.size != size or existing.sha1 != sha1 or (existing.sha256 not in (None, sha256)):
                raise RemoteError(rc.Exit.BACKUP, f"{key} exists remotely with different content ({'just uploaded' if uploaded else 'conflict'}); "
                                                  "nothing is overwritten")
            objects.append({"name": key, "tier": tier, "size": size, "sha1": sha1, "sha256": sha256, "file_id": existing.file_id,
                            "uploaded": uploaded})
    return {"schema_version": 1, "backup_id": backup_id, "status": "verified", "provider": store.cfg.provider, "bucket": store.cfg.bucket,
            "verified_at": now(), "objects": objects}


# --- dead-man's switch -----------------------------------------------------------------------------

def ping_deadman(url: str, *, transport: Transport = https_transport, rehearsal: bool = False) -> bool:
    """One GET to an external dead-man's switch (e.g. healthchecks.io) after a
    fully successful run. Missing pings are what raise the external alarm, so
    a failed ping only warns; it never fails the backup."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" and not (rehearsal and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"):
        return False
    for _attempt in range(2):
        try:
            status, _ = transport("GET", url, {"User-Agent": f"artesa-backup/{rc.TOOL_VERSION}"}, None, 15.0)
        except (urllib.error.URLError, OSError, ssl.SSLError):
            continue
        if 200 <= status < 300:
            return True
    return False
