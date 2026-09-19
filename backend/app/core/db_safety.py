"""Environment and database-target safety policy (post-Sprint 4 hardening).

One implementation shared by runtime settings validation
(``app.core.config``), the seed CLI (``app.db.seed``) and the pytest session
guard (``tests/conftest.py``), so the rules cannot drift apart.

Secret safety: nothing here ever puts a DATABASE_URL, user name, password or
host into an exception message, and the parsed ``DatabaseTarget`` carries no
credentials. Errors are ``UnsafeConfigurationError`` (a ``RuntimeError``, not
a ``ValueError`` on purpose): pydantic echoes ``input_value`` -- which would
include DATABASE_URL and its password -- for ``ValueError`` raised inside a
validator, but lets any other exception propagate untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.engine import URL, make_url

ENV_LOCAL = "local"
ENV_TEST = "test"
ENV_STAGING = "staging"
ENV_PRODUCTION = "production"

# APP_ENV is required and must be exactly one of these (after trim +
# lowercase). No aliases: "dev", "development" and "prod" are rejected so a
# deployment typo cannot silently bypass the production guards.
VALID_ENVS = (ENV_LOCAL, ENV_TEST, ENV_STAGING, ENV_PRODUCTION)

# The seed writes fictional demo data and may only ever run in these.
SEED_ALLOWED_ENVS = (ENV_LOCAL, ENV_TEST)

# The well-known development database URL. Settings.database_url has NO
# default any more (DATABASE_URL must be configured explicitly; see
# assert_database_url_configured), so this is not applied anywhere at runtime.
# It stays only as a reference for the staging/production guard, which refuses
# it if someone configures it explicitly.
DEFAULT_DATABASE_URL = "postgresql://artesanfc:artesanfc@localhost:5432/artesanfc"

# Credentials that only ever appear in the dev default, .env.example and
# docker-compose.yml. staging/production must not use them.
PLACEHOLDER_PASSWORDS = frozenset({"artesanfc", "change-me"})

# Hosts accepted for seeding under APP_ENV=local: loopback, the docker
# compose `db` service name (used by .env.example) and unix sockets.
LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db"})

# A database counts as a test database only when its name has a `test` token
# delimited by `_` or `-` (artesanfc_test, test_artesanfc, ci-test-db). This
# deliberately does not match substrings such as "contest" or "latest".
_TEST_DB_NAME_RE = re.compile(r"(?:^|[_-])test(?:$|[_-])", re.IGNORECASE)


class UnsafeConfigurationError(RuntimeError):
    """Configuration or database target refused by the safety policy.

    Messages are credential-free by construction; see the module docstring.
    """


@dataclass(frozen=True)
class DatabaseTarget:
    """A parsed database URL reduced to what the safety rules need.

    Deliberately holds no user name, password or full URL so it can never
    leak them through ``repr`` or an error message.
    """

    hosts: tuple[str, ...]
    database: str | None


def _parse_url(url: object) -> URL:
    try:
        return make_url(url)  # type: ignore[arg-type]
    except Exception:
        # `from None`: never chain the original error, whatever a driver or
        # SQLAlchemy version might echo from the input.
        raise UnsafeConfigurationError(
            "DATABASE_URL could not be parsed as a database URL (value not shown)."
        ) from None


def _host_values(url: URL) -> tuple[str, ...]:
    """Every host the driver could connect to, including libpq-style
    ``?host=`` / ``?hostaddr=`` query parameters (which can point at a remote
    server even when the URL authority has no host)."""
    hosts: list[str] = []
    if url.host:
        hosts.append(url.host)
    for key in ("host", "hostaddr"):
        raw = url.query.get(key)
        values = [raw] if isinstance(raw, str) else list(raw or ())
        for value in values:
            hosts.extend(part.strip() for part in value.split(",") if part.strip())
    return tuple(hosts)


def parse_database_target(url: str) -> DatabaseTarget:
    parsed = _parse_url(url)
    return DatabaseTarget(hosts=_host_values(parsed), database=parsed.database)


def normalize_app_env(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if not value:
        raise UnsafeConfigurationError(
            "APP_ENV is not set. Set it explicitly to one of: "
            + ", ".join(VALID_ENVS)
            + " (environment variable or backend/.env)."
        )
    if value not in VALID_ENVS:
        raise UnsafeConfigurationError(
            f"APP_ENV has unsupported value {value[:32]!r}. Accepted values: "
            + ", ".join(VALID_ENVS)
            + " (no aliases such as dev, development or prod)."
        )
    return value


def is_test_database_name(name: str | None) -> bool:
    return bool(name) and _TEST_DB_NAME_RE.search(name) is not None


def _is_local_host(host: str) -> bool:
    # A leading "/" is a unix-socket directory (libpq `host=/var/run/...`).
    return host.lower() in LOCAL_DB_HOSTS or host.startswith("/")


def assert_database_url_configured(url: str | None) -> None:
    """Fail closed when DATABASE_URL is unset, empty or whitespace-only: there
    is no implicit default database. Only presence is checked (format and
    target rules live in the other guards) and the value is never modified.
    The message is static, so it cannot leak anything."""
    if url is None or not url.strip():
        raise UnsafeConfigurationError(
            "DATABASE_URL is not set. Set it explicitly (environment variable "
            "or backend/.env); there is no default database."
        )


def assert_production_grade_database(url: str, app_env: str) -> None:
    """staging/production: refuse the development default database URL or any
    placeholder/empty password (which also covers the docker-compose default
    and .env.example)."""
    parsed = _parse_url(url)
    default = make_url(DEFAULT_DATABASE_URL)
    is_default = (
        parsed.username == default.username
        and parsed.password == default.password
        and parsed.database == default.database
        and parsed.host == default.host
        and parsed.port == default.port
    )
    password = parsed.password
    has_placeholder_password = not password or password.lower() in PLACEHOLDER_PASSWORDS
    if is_default or has_placeholder_password:
        raise UnsafeConfigurationError(
            f"Refusing to start with APP_ENV={app_env}: DATABASE_URL is the "
            "development default or uses an empty/placeholder password. "
            "Configure a real database URL (value not shown)."
        )


def assert_safe_for_tests(app_env: str | None, url: str) -> None:
    """The pytest session may write (seed fixtures are committed), so it must
    target an explicitly marked test database: APP_ENV=test AND a test-marked
    database name. Call before anything touches the database."""
    env = (app_env or "").strip().lower()
    if env != ENV_TEST:
        raise UnsafeConfigurationError(
            f"Refusing to run tests: APP_ENV is {env[:32]!r} but the test suite "
            "requires APP_ENV=test. No database was touched."
        )
    name = parse_database_target(url).database
    if not is_test_database_name(name):
        raise UnsafeConfigurationError(
            f"Refusing to run tests: database name {(name or '(none)')[:64]!r} is "
            "not marked as a test database (its name needs a 'test' token "
            "delimited by '_' or '-', e.g. 'artesanfc_test'). Point DATABASE_URL "
            "at a dedicated test database. No database was touched."
        )


def assert_safe_for_seed(app_env: str | None, url: str) -> None:
    """The demo seed may only run with APP_ENV=local or APP_ENV=test, and only
    against a clearly non-production target: a test-marked database under
    ``test``, a local host under ``local``."""
    env = (app_env or "").strip().lower()
    if env not in SEED_ALLOWED_ENVS:
        raise UnsafeConfigurationError(
            f"Refusing to seed: APP_ENV is {env[:32]!r}; seeding is only allowed "
            "with APP_ENV=local or APP_ENV=test. No database was touched."
        )
    target = parse_database_target(url)
    if env == ENV_TEST:
        if not is_test_database_name(target.database):
            raise UnsafeConfigurationError(
                "Refusing to seed: with APP_ENV=test the database name must be "
                "marked as a test database (a 'test' token delimited by '_' or "
                "'-'). No database was touched."
            )
        return
    if not all(_is_local_host(host) for host in target.hosts):
        raise UnsafeConfigurationError(
            "Refusing to seed: with APP_ENV=local the database host must be a "
            "local development host (localhost, 127.0.0.1, ::1, db or a unix "
            "socket). No database was touched."
        )
