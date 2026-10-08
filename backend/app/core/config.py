import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.db_safety import (
    ENV_LOCAL,
    ENV_PRODUCTION,
    ENV_STAGING,
    ENV_TEST,
    UnsafeConfigurationError,
    assert_database_url_configured,
    assert_production_grade_database,
    normalize_app_env,
)

# Anchored to this file (backend/app/core/config.py -> backend/), not to the
# process working directory: starting uvicorn/alembic/pytest from another
# directory must not change which .env is read (or silently read none and
# fall back to development defaults). The file is optional; real environment
# variables always take priority over it.
BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_DIR / ".env"

# The one browser origin production must allow (docs/SECURITY.md section 10).
PRODUCTION_FRONTEND_ORIGIN = "https://artesanfc.com"
# N-01: the only browser origins production may allow. The apex is required;
# www is tolerated (the deploy rehearsals use it). Anything else -- localhost,
# plain http, a preview host -- refuses to start.
PRODUCTION_ALLOWED_ORIGINS = frozenset({PRODUCTION_FRONTEND_ORIGIN, "https://www.artesanfc.com"})

_ACCESS_TEAM_DOMAIN_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com")
_ACCESS_AUD_RE = re.compile(r"[0-9a-f]{64}")
_EMAIL_RE = re.compile(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+")


class Settings(BaseSettings):
    app_name: str = "ArtesaNFC API"

    # Required, no default: one of local | test | staging | production (see
    # app/core/db_safety.py). An unset APP_ENV must fail loudly instead of
    # silently behaving like local development. Declared as a plain str with
    # an empty default (not a required field) on purpose: pydantic's
    # "field required" error would echo the whole input, DATABASE_URL and its
    # password included. The check lives in _validate_environment below.
    app_env: str = ""
    debug: bool = False

    # Required, with no default database: an unset, empty or whitespace-only
    # DATABASE_URL fails in _validate_environment below (after APP_ENV). Same
    # reason as app_env: a required pydantic field would produce a "field
    # required" ValidationError that echoes the whole input (the dotenv
    # POSTGRES_* keys, and DATABASE_URL when set). The empty default is never
    # used as a value.
    # repr=False keeps the URL (and its password) out of repr(settings),
    # tracebacks and pytest failure output.
    database_url: str = Field(default="", repr=False)

    # Explicit CORS allowlist (docs/SECURITY.md section 10: never "*", never
    # a permissive regex). Comma-separated so a plain .env value stays
    # human-editable, instead of requiring JSON-encoding a list in one env
    # var. Default covers local development only (VS Code Live Server, the
    # standard tool for this repo's build-step-free static frontend, per
    # docs/ARCHITECTURE.md section 9); APP_ENV=production refuses to start
    # unless this contains https://artesanfc.com, e.g.
    # CORS_ALLOWED_ORIGINS=https://artesanfc.com.
    cors_allowed_origins: str = "http://127.0.0.1:5500,http://localhost:5500"

    # Gestión admin API (ADR-029). It is enabled only when all three are set;
    # with none of them every admin GET answers the same 404 as an unknown
    # route. A partial configuration refuses to start (below).
    # Team domain of the Cloudflare Access team, e.g.
    # artesanfc.cloudflareaccess.com: the JWT issuer is https://<domain> and
    # its signing keys come from https://<domain>/cdn-cgi/access/certs.
    admin_access_team_domain: str = ""
    # The Access application's AUD tag (64 hex characters). Not a secret, but
    # it binds a token to this one application.
    admin_access_aud: str = ""
    # Comma-separated allowlist, compared case-insensitively. Access decides
    # who may sign in; this list decides who may use the admin API.
    admin_emails: str = ""
    # ADR-030 roles. Both lists must be subsets of ADMIN_EMAILS; everyone in
    # ADMIN_EMAILS is an editor. Custodians generate tokens and card keys,
    # write tags and see certificates and NFC tags; designers design
    # certificates. Custodians are also designers.
    custodian_emails: str = ""
    designer_emails: str = ""
    # P-026 G3: an artisan is published only with their authorization
    # (asked by WhatsApp or recorded in person). Tests switch it off.
    require_artisan_authorization: bool = True
    # P-026 G4: the owner manages Gestión's accounts from its "Usuarios" page.
    # Fixed here (never editable from Gestión) so nobody can lock them out.
    owner_emails: str = ""
    # Optional: with these, adding or removing an account in Gestión also
    # updates the Cloudflare Access group that Gestión's policy includes.
    # The token needs only "Access: Organizations, Identity Providers, and
    # Groups: Edit". Never logged.
    access_sync_api_token: str = ""
    access_sync_account_id: str = ""
    access_sync_group_id: str = ""
    # AUD tag of the separate Access application that guards the custody
    # path (/api/admin/v1/custody). Optional: tokens of either application
    # are accepted, the custodian role is still checked here.
    custody_access_aud: str = ""

    # Gestión phase 4 (docs/MEDIA.md): absolute path of the media directory,
    # which holds originales/ (private, never served) and publico/ (the only
    # thing /media/ serves). Unset: /media/ answers 404 and uploads 503.
    media_root: str = ""

    # .env.example (and any .env copied from it) also carries the discrete
    # POSTGRES_USER/PASSWORD/DB/HOST/PORT vars consumed directly by
    # docker-compose.yml for the `db` service; Settings only needs the
    # already-assembled DATABASE_URL, so those extra keys must be ignored
    # here rather than rejected.
    # hide_input_in_errors: belt and braces so no ValidationError (e.g. a bad
    # DEBUG value) ever prints input_value/input_type. Note errors() still
    # carries the input; nothing here logs it.
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def _validate_environment(self) -> "Settings":
        # Every failure here raises UnsafeConfigurationError (a RuntimeError,
        # not a ValueError) so pydantic does not wrap it in a ValidationError
        # that echoes input_value -- i.e. DATABASE_URL with its password.
        self.app_env = normalize_app_env(self.app_env)
        # After APP_ENV (which says what we are starting as), before the
        # staging/production guards (which need a URL to inspect).
        assert_database_url_configured(self.database_url)

        if self.app_env in (ENV_STAGING, ENV_PRODUCTION):
            assert_production_grade_database(self.database_url, self.app_env)

        self._validate_admin_access()
        self._validate_media_root()

        if self.app_env == ENV_PRODUCTION:
            if PRODUCTION_FRONTEND_ORIGIN not in self.cors_allowed_origins_list:
                raise UnsafeConfigurationError(
                    "Refusing to start with APP_ENV=production: "
                    f"CORS_ALLOWED_ORIGINS must include {PRODUCTION_FRONTEND_ORIGIN}."
                )
            extra = sorted(set(self.cors_allowed_origins_list) - PRODUCTION_ALLOWED_ORIGINS)
            if extra:
                raise UnsafeConfigurationError(
                    "Refusing to start with APP_ENV=production: CORS_ALLOWED_ORIGINS may only list "
                    f"{', '.join(sorted(PRODUCTION_ALLOWED_ORIGINS))}; remove {', '.join(extra)}."
                )
            if self.debug:
                raise UnsafeConfigurationError(
                    "Refusing to start with APP_ENV=production: DEBUG must be "
                    "false (docs/SECURITY.md)."
                )
        return self

    def _validate_admin_access(self) -> None:
        self.admin_access_team_domain = self.admin_access_team_domain.strip().lower()
        self.admin_access_aud = self.admin_access_aud.strip().lower()
        configured = [
            bool(self.admin_access_team_domain),
            bool(self.admin_access_aud),
            bool(self.admin_emails_list),
        ]
        if not any(configured):
            return
        if not all(configured):
            raise UnsafeConfigurationError(
                "Refusing to start with a partial admin configuration: set all of "
                "ADMIN_ACCESS_TEAM_DOMAIN, ADMIN_ACCESS_AUD and ADMIN_EMAILS, or none."
            )
        if not _ACCESS_TEAM_DOMAIN_RE.fullmatch(self.admin_access_team_domain):
            raise UnsafeConfigurationError(
                "ADMIN_ACCESS_TEAM_DOMAIN must be <team>.cloudflareaccess.com (no scheme, no path)."
            )
        if not _ACCESS_AUD_RE.fullmatch(self.admin_access_aud):
            raise UnsafeConfigurationError("ADMIN_ACCESS_AUD must be the 64-hex-character AUD tag.")
        if any(not _EMAIL_RE.fullmatch(email) for email in self.admin_emails_list):
            raise UnsafeConfigurationError("ADMIN_EMAILS must be a comma-separated list of email addresses.")
        admins = set(self.admin_emails_list)
        for name, emails in (("CUSTODIAN_EMAILS", self.custodian_emails_list),
                             ("DESIGNER_EMAILS", self.designer_emails_list),
                             ("OWNER_EMAILS", self.owner_emails_list)):
            if any(not _EMAIL_RE.fullmatch(email) for email in emails):
                raise UnsafeConfigurationError(f"{name} must be a comma-separated list of email addresses.")
            if not set(emails) <= admins:
                raise UnsafeConfigurationError(f"Every address in {name} must also be in ADMIN_EMAILS.")
        self.custody_access_aud = self.custody_access_aud.strip().lower()
        if self.custody_access_aud and not _ACCESS_AUD_RE.fullmatch(self.custody_access_aud):
            raise UnsafeConfigurationError("CUSTODY_ACCESS_AUD must be the 64-hex-character AUD tag.")

    def _validate_media_root(self) -> None:
        self.media_root = self.media_root.strip()
        if not self.media_root:
            return
        root = Path(self.media_root)
        if not root.is_absolute():
            raise UnsafeConfigurationError("MEDIA_ROOT must be an absolute path.")
        missing = [name for name in ("originales", "publico") if not (root / name).is_dir()]
        if missing:
            raise UnsafeConfigurationError(
                "MEDIA_ROOT must contain the directories originales/ and publico/ (docs/MEDIA.md section 2)."
            )

    @property
    def media_enabled(self) -> bool:
        return bool(self.media_root)

    @property
    def media_originals_dir(self) -> Path:
        return Path(self.media_root) / "originales"

    @property
    def media_public_dir(self) -> Path:
        return Path(self.media_root) / "publico"

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_access_team_domain and self.admin_access_aud and self.admin_emails_list)

    @property
    def admin_emails_list(self) -> list[str]:
        return [email.strip().lower() for email in self.admin_emails.split(",") if email.strip()]

    @property
    def custodian_emails_list(self) -> list[str]:
        return [email.strip().lower() for email in self.custodian_emails.split(",") if email.strip()]

    @property
    def owner_emails_list(self) -> list[str]:
        return [email.strip().lower() for email in self.owner_emails.split(",") if email.strip()]

    @property
    def access_sync_enabled(self) -> bool:
        return bool(self.access_sync_api_token and self.access_sync_account_id and self.access_sync_group_id)

    @property
    def designer_emails_list(self) -> list[str]:
        return [email.strip().lower() for email in self.designer_emails.split(",") if email.strip()]

    @property
    def docs_enabled(self) -> bool:
        """Interactive docs (/docs, /redoc, /docs/oauth2-redirect) and the
        OpenAPI schema (/openapi.json) are a local/test convenience only:
        staging and production do not serve them at all (docs/SECURITY.md
        section 13, docs/OPERATIONS.md). Deciding it here, in the app, keeps
        it independent of whatever sits in front of the API."""
        return self.app_env in (ENV_LOCAL, ENV_TEST)

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
