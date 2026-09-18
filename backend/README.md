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
   real secrets).

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

   Safe to re-run: the seed is idempotent (`docs/SPRINT_3.md`).

6. **Run the API**

   ```bash
   uvicorn app.main:app --reload
   ```

   `GET http://localhost:8000/health` should report
   `{"status": "ok", "database": "connected"}`.

7. **Run the test suite**

   ```bash
   pytest -q
   ```

   Tests run against the same `DATABASE_URL` as the app (see
   `app/core/config.py`), so PostgreSQL must be up and migrated first.

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
