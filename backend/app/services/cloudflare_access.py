"""P-026 G4: keep a Cloudflare Access group in step with Gestión's accounts.

Gestión's Access policy includes one Access group; this module replaces the
group's email rules with the current set of people (ADMIN_EMAILS plus the
active accounts). Other kinds of rules in the group are kept.

Standard library only (urllib). The API token is sent in the Authorization
header and never logged or returned; errors carry only Cloudflare's codes.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
TIMEOUT_SECONDS = 10


class SyncError(Exception):
    pass


def _call(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "artesanfc-gestion",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        codes = ", ".join(str(e.get("code")) for e in payload.get("errors", [])) or str(exc.code)
        raise SyncError(f"Cloudflare respondió {exc.code} ({codes})") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise SyncError("No se pudo contactar a Cloudflare") from None
    if not payload.get("success", False):
        raise SyncError("Cloudflare rechazó el cambio")
    return payload.get("result") or {}


def sync_group(*, token: str, account_id: str, group_id: str, emails: set[str]) -> int:
    """Makes the group's email rules exactly ``emails``. Returns how many."""
    url = f"{API}/accounts/{account_id}/access/groups/{group_id}"
    group = _call("GET", url, token)
    others = [rule for rule in group.get("include", []) if "email" not in rule]
    include = others + [{"email": {"email": e}} for e in sorted(emails)]
    body = {"name": group.get("name") or "Gestión", "include": include}
    for key in ("exclude", "require", "is_default"):
        if group.get(key):
            body[key] = group[key]
    _call("PUT", url, token, body)
    return len(emails)
