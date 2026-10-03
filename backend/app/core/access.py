"""Cloudflare Access identity for the Gestión admin API (ADR-029).

Access sits in front of the admin hostname and signs every request it lets
through with a JWT in the ``Cf-Access-Jwt-Assertion`` header. The API does not
trust that the request came through Access: it verifies the token itself
(RS256 signature against the team's published keys, audience, issuer,
expiry) and then checks the email against its own allowlist. A process on
the host that reaches 127.0.0.1:8000 directly has no valid token and gets 401.

Only the header is read, never the ``CF_Authorization`` cookie: a header is
not sent by the browser on its own, so a cross-site request cannot carry it.
The token is never logged or echoed.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request

from app.api.v1.common import not_found
from app.core.config import get_settings

ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"

# Access tokens are RS256 only; accepting anything else would let a token pick
# its own verification algorithm.
_ALGORITHMS = ["RS256"]
_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "email"]
# Small tolerance for clock skew between Cloudflare and this host.
_LEEWAY_SECONDS = 30
# Access rotates its signing keys every six weeks and publishes the next key in
# advance; an hour of caching still picks a new key up long before it is used.
_JWKS_LIFESPAN_SECONDS = 3600
_JWKS_TIMEOUT_SECONDS = 5

UNAUTHENTICATED_ERROR = {"code": "unauthenticated", "message": "Authentication is required."}
FORBIDDEN_ERROR = {"code": "forbidden", "message": "This account is not allowed to use this resource."}
AUTH_UNAVAILABLE_ERROR = {
    "code": "auth_unavailable",
    "message": "Authentication is temporarily unavailable.",
}


EDITOR, DESIGNER, CUSTODIAN = "editor", "designer", "custodian"


@dataclass(frozen=True)
class AdminIdentity:
    email: str
    # ADR-030: everyone allowed is an editor; designer and custodian come from
    # their own allowlists (a custodian is also a designer).
    roles: frozenset[str] = frozenset({EDITOR})

    def has(self, role: str) -> bool:
        return role in self.roles


class AccessUnavailable(Exception):
    """The signing keys could not be fetched; nothing can be verified."""


class AccessDenied(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.status_code = status_code


class AccessVerifier:
    def __init__(
        self,
        team_domain: str,
        audience: str,
        allowed_emails: tuple[str, ...],
        jwks_client: jwt.PyJWKClient | None = None,
        *,
        custodians: tuple[str, ...] = (),
        designers: tuple[str, ...] = (),
        extra_audiences: tuple[str, ...] = (),
    ) -> None:
        self.issuer = f"https://{team_domain}"
        # The custody path may sit behind its own Access application: its
        # tokens carry that application's AUD (ADR-030).
        self.audience = [audience, *[a for a in extra_audiences if a]]
        self.allowed_emails = frozenset(email.lower() for email in allowed_emails)
        self.custodians = frozenset(email.lower() for email in custodians)
        self.designers = frozenset(email.lower() for email in designers) | self.custodians
        self._jwks = jwks_client or jwt.PyJWKClient(
            f"{self.issuer}/cdn-cgi/access/certs",
            cache_keys=True,
            lifespan=_JWKS_LIFESPAN_SECONDS,
            timeout=_JWKS_TIMEOUT_SECONDS,
            headers={"User-Agent": "artesanfc-admin-api"},
        )

    def verify(self, token: str) -> AdminIdentity:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError:
            raise AccessUnavailable() from None
        except (jwt.PyJWKClientError, jwt.PyJWKError, jwt.DecodeError):
            # Malformed token, or a kid that is not in the team's key set.
            raise AccessDenied(401) from None

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=_ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                leeway=_LEEWAY_SECONDS,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError:
            raise AccessDenied(401) from None

        email = claims.get("email")
        if not isinstance(email, str) or email.strip().lower() not in self.allowed_emails:
            raise AccessDenied(403)
        email = email.strip().lower()
        roles = {EDITOR}
        if email in self.designers:
            roles.add(DESIGNER)
        if email in self.custodians:
            roles.add(CUSTODIAN)
        return AdminIdentity(email=email, roles=frozenset(roles))


@lru_cache
def _verifier_for(team_domain: str, audience: str, allowed_emails: tuple[str, ...],
                  custodians: tuple[str, ...], designers: tuple[str, ...],
                  extra_audiences: tuple[str, ...]) -> AccessVerifier:
    # One verifier (and one key cache) per configuration for the process.
    return AccessVerifier(team_domain, audience, allowed_emails, custodians=custodians,
                          designers=designers, extra_audiences=extra_audiences)


def get_access_verifier() -> AccessVerifier | None:
    """None when the admin API is not configured (ADR-029)."""
    settings = get_settings()
    if not settings.admin_enabled:
        return None
    return _verifier_for(
        settings.admin_access_team_domain,
        settings.admin_access_aud,
        tuple(settings.admin_emails_list),
        tuple(settings.custodian_emails_list),
        tuple(settings.designer_emails_list),
        (settings.custody_access_aud,) if settings.custody_access_aud else (),
    )


def require_admin(
    request: Request,
    verifier: AccessVerifier | None = Depends(get_access_verifier),
) -> AdminIdentity:
    if verifier is None:
        # Not configured: the same 404 body as a route that does not exist.
        raise not_found()
    token = request.headers.get(ACCESS_JWT_HEADER, "").strip()
    if not token:
        raise HTTPException(status_code=401, detail=UNAUTHENTICATED_ERROR)
    try:
        return verifier.verify(token)
    except AccessUnavailable:
        raise HTTPException(status_code=503, detail=AUTH_UNAVAILABLE_ERROR) from None
    except AccessDenied as exc:
        detail = FORBIDDEN_ERROR if exc.status_code == 403 else UNAUTHENTICATED_ERROR
        raise HTTPException(status_code=exc.status_code, detail=detail) from None
