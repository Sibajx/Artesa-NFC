# ArtesaNFC — Backend (Sprint 3)

FastAPI + PostgreSQL backend for ArtesaNFC's public API. See
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md),
[`../docs/DATA_MODEL.md`](../docs/DATA_MODEL.md) and
[`../docs/API_CONTRACT.md`](../docs/API_CONTRACT.md) for the approved
design this implements; this file only documents how to run it locally.

## Local development workflow (Sprint 3, supported today)

This is the actual sequence used to develop and verify this backend
today. It intentionally runs migrations and the seed from the host
Python environment, not from inside the Docker image — see
[Docker scope](#docker-scope-for-sprint-3) below.

1. **Environment file**

   ```bash
   cp .env.example .env
   ```

   Adjust values if needed (`.env` is git-ignored; `.env.example` has no
   real secrets). `APP_ENV` is **required** — keep `APP_ENV=local` from the
   example for local development; the app refuses to start without it
   (see [Environment safety](#environment-safety)). The file is always
   read from `backend/.env`, wherever you start `uvicorn`/`alembic` from,
   and a real environment variable (e.g. `export APP_ENV=local`) always
   overrides it. `.env` itself is optional.

2. **Start PostgreSQL**

   ```bash
   docker compose up -d db
   ```

   This starts only the `db` service (Postgres 16) on
   `127.0.0.1:5432`, using the credentials from `.env`.

   **Host vs. container hostname:** `.env`'s `DATABASE_URL` uses `db`
   as the hostname, which only resolves *inside* the Compose network
   (that's what the `api` service uses). Steps 4, 5 and 7 below run
   from the host, not inside a container, so they need `localhost`
   instead. Export an override before running them:

   ```bash
   export DATABASE_URL=postgresql://artesanfc:change-me@localhost:5432/artesanfc
   ```

   (matching whatever user/password/db name you set in `.env`). This
   overrides `.env` for the current shell only; `docker compose` itself
   is unaffected and keeps using `db` for the `api` container.

3. **Install backend requirements in a local Python environment**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Apply migrations**

   ```bash
   alembic upgrade head
   ```

5. **Seed the deterministic demo fixtures**

   ```bash
   python -m app.db.seed
   ```

   Safe to re-run: the seed is idempotent (`docs/SPRINT_3.md`). It
   refuses to run unless `APP_ENV` is `local` or `test` (see
   [Environment safety](#environment-safety)).

6. **Run the API**

   ```bash
   uvicorn app.main:app --reload
   ```

   `GET http://localhost:8000/health` should report
   `{"status": "ok", "database": "connected"}`.

7. **Run the test suite** (needs its own test database)

   Some tests commit real rows (the demo seed fixtures), so the suite
   refuses to start unless `APP_ENV=test` **and** the database name has
   a `test` token. Create a dedicated database once, migrate it, then run
   the tests against it:

   ```bash
   docker compose exec db createdb -U artesanfc artesanfc_test
   export APP_ENV=test
   export DATABASE_URL=postgresql://artesanfc:change-me@localhost:5432/artesanfc_test
   alembic upgrade head
   pytest -q
   ```

   Use a new shell (or `unset APP_ENV DATABASE_URL`) afterwards, or your
   `alembic`/`uvicorn` runs will target the test database. A bare
   `pytest -q` against your development database is refused on purpose.

## Local frontend / CORS

Port `5500` is the project convention for the local static frontend
(e.g. VS Code Live Server) — the default `CORS_ALLOWED_ORIGINS` in
`.env.example` allows both `http://127.0.0.1:5500` and
`http://localhost:5500` out of the box. If the frontend is intentionally
served from a different origin, override `CORS_ALLOWED_ORIGINS` (comma
separated) in `.env`. With `APP_ENV=production` the API refuses to
start unless `CORS_ALLOWED_ORIGINS` contains `https://artesanfc.com`.

## Environment safety

Post-Sprint 4 hardening (audit findings F-02 and F-05). Implemented in
`app/core/config.py` and `app/core/db_safety.py`.

**`APP_ENV`** is required and has no default. Accepted values (trimmed and
lowercased; no aliases — `dev`, `development`, `prod` are rejected with a
clear error so a typo cannot bypass the guards):

| Value | Meaning |
|---|---|
| `local` | Local development. Development defaults are allowed. |
| `test` | The pytest suite. |
| `staging` | Refuses the development default `DATABASE_URL` / placeholder password. |
| `production` | Everything `staging` checks, plus the rules below. |

**Production guard.** With `APP_ENV=production` startup (API, `alembic`,
seed) fails fast, without printing the database URL, if:

- `DATABASE_URL` is the development default or has an empty/placeholder
  password (`change-me`, `artesanfc`);
- `CORS_ALLOWED_ORIGINS` does not contain exactly `https://artesanfc.com`
  (a localhost-only list is rejected);
- `DEBUG` is true.

**Test database policy.** The pytest session aborts before collecting any
test unless `APP_ENV=test` **and** the `DATABASE_URL` database name has a
`test` token delimited by `_` or `-` (`artesanfc_test`, `test_artesanfc`;
`contest` and `latest` do not qualify). `APP_ENV` is never set for you: export
it explicitly.

**CI.** `.github/workflows/backend-ci.yml` (validation only, no deployment)
runs on pull requests into `develop` and pushes to `develop`. It starts a
disposable PostgreSQL 16 service with a test-marked database
(`artesanfc_test`), exports `APP_ENV=test` and the matching `DATABASE_URL`,
then runs `alembic upgrade head`, `alembic check` and `pytest -q` from
`backend/` on Python 3.12 (the Dockerfile's version).

**Seed policy.** `python -m app.db.seed` requires `APP_ENV=local` or
`test`. Under `test` the database name must be test-marked; under `local`
the host must be `localhost`, `127.0.0.1`, `::1`, the compose service `db`,
or a unix socket. `production` and `staging` are always refused, before any
connection is opened.

**`.env` location.** `backend/.env`, resolved from the code's own path — not
from the working directory. It is optional; exported environment variables
take priority over it.

**Not covered here:** `alembic upgrade` runs against whatever
`DATABASE_URL` is configured (it is not seed/test guarded), and an extra
localhost origin next to `https://artesanfc.com` in production CORS is not
rejected.

## Docker scope for Sprint 3

`docker-compose.yml` currently starts two services: `db` (PostgreSQL)
and `api` (this backend, built from `Dockerfile`). The `api` image runs
`uvicorn` directly — **it does not run Alembic migrations or the seed
automatically**, and the image does not currently include the
`alembic/` directory or `alembic.ini`.

This is intentional for Sprint 3 local development, not an oversight:
migrations and seeding are run from the host environment (steps 4–5
above) against the same `db` service the `api` container also uses.
This keeps the container image minimal for now and avoids adding
migration-on-boot logic before the deployment story for staging/
production is designed.

**This is not a production deployment workflow.** No claim is made here
about how migrations, seeding, or the API container should run in
staging or production — that is out of scope for this document and for
Sprint 3.

## Sprint boundary

This backend implements Sprint 3 scope only: `Artisan`, `Piece`,
`MediaAsset`, and the public read-only `/api/v1/artisans` and
`/api/v1/pieces` endpoints. Certificates, NFC tags, ownership, and
`audit_event` belong to Sprint 4 (`../docs/WORKFLOW.md` §14) and are not
implemented here.
