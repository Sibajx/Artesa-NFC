#!/usr/bin/env bash
# Repeatable browser QA for the private certificate route /c/{token}
# (audit finding F-06; docs/QA_PRIVATE_ROUTE.md).
#
#   ./qa/validate-private-route.sh                   built-in production-like server (default)
#   QA_SERVER=wrangler ./qa/validate-private-route.sh  same checks against wrangler pages dev
#
# It starts, on loopback only and under a per-run name: a disposable PostgreSQL
# container, the real FastAPI app on 127.0.0.1:8000, and the real frontend/ tree
# behind a server that honours frontend/_redirects and _headers. It then runs
# one Python process that creates synthetic data through the real certificate
# lifecycle (tokens stay in that process's memory), drives Chromium through
# /c/{token} and checks the public isolation. No production credentials, no
# production data, nothing is installed for you.
#
# Everything it creates is removed on exit, also on failure, INT and TERM:
# containers, server process groups, the temp dir (and with it any browser
# profile and server logs). Exit: 0 = pass, 1 = a QA check failed, 2 = setup or
# prerequisite problem.
#
# Test hooks (not needed for normal use):
#   QA_FRONTEND_DIR  frontend tree to test instead of ./frontend (mutation checks
#                    run against temp copies; the working tree is never edited)
#   PYTHON           interpreter with the backend + qa requirements (default python3)
#   POSTGRES_IMAGE   default postgres:16 (must already be pulled)
#   WRANGLER_BIN     wrangler executable for QA_SERVER=wrangler
#   QA_WRANGLER_RAM_DIR  base for Wrangler's state (default /dev/shm). Wrangler mode REQUIRES
#                    RAM-backed (tmpfs/ramfs) storage and fails closed (exit 2) otherwise
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
QA_DIR=$SCRIPT_DIR
BACKEND_DIR=$REPO_ROOT/backend
FRONTEND_DIR=${QA_FRONTEND_DIR:-$REPO_ROOT/frontend}
PYTHON=${PYTHON:-python3}
QA_SERVER=${QA_SERVER:-builtin}
POSTGRES_IMAGE=${POSTGRES_IMAGE:-postgres:16}
WRANGLER_PIN=4.135.0
API_HOST=127.0.0.1
API_PORT=8000

RUN_ID="$(date +%s)-$$"
PREFIX="artesanfc-qa-route-$RUN_ID"
# Every process this run starts inherits this variable, so leftovers can be found by
# an exact, per-run marker even when they left our process groups (Playwright starts
# Chromium in its own session).
export ARTESANFC_QA_RUN="$RUN_ID"
PG=""
TMP=""
WR_STATE=""
API_PID=""
FE_PID=""
QA_PID=""
WRANGLER_CMD=()

say()  { printf '%s\n' "$*"; }
step() { printf '\n>> %s\n' "$*"; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 2; }

# --- cleanup (EXIT, also reached through INT/TERM) -----------------------------
# Servers and the QA runner are started with setsid, so each one leads its own
# process group; they are stopped by group id (never by name pattern), which also
# takes down grandchildren such as workerd and Chromium.
stop_group() {
  local pid=$1 i
  [ -n "$pid" ] || return 0
  kill -TERM -- "-$pid" 2>/dev/null || return 0
  for i in $(seq 1 50); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  kill -KILL -- "-$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

# Kill every remaining process that carries this run's marker (Linux /proc). Exact
# match on the run id: it cannot touch anything this run did not start.
sweep_marked() {
  local signal=$1 d pid
  [ -d /proc/self ] || return 0
  for d in /proc/[0-9]*; do
    pid=${d#/proc/}
    [ "$pid" = "$$" ] && continue
    if tr '\0' '\n' <"$d/environ" 2>/dev/null | grep -qx "ARTESANFC_QA_RUN=$RUN_ID"; then
      kill "-$signal" "$pid" 2>/dev/null
    fi
  done
}

marked_left() {
  local d pid n=0
  [ -d /proc/self ] || { echo 0; return; }
  for d in /proc/[0-9]*; do
    pid=${d#/proc/}
    [ "$pid" = "$$" ] && continue
    [ -r "$d/environ" ] || continue
    if tr '\0' '\n' <"$d/environ" 2>/dev/null | grep -qx "ARTESANFC_QA_RUN=$RUN_ID"; then n=$((n + 1)); fi
  done
  echo "$n"
}

cleanup() {
  local rc=$?
  trap '' INT TERM
  set +e
  stop_group "$QA_PID"
  stop_group "$FE_PID"
  stop_group "$API_PID"
  sweep_marked TERM
  sleep 0.3
  sweep_marked KILL
  [ -n "$PG" ] && docker rm -f -v "$PG" >/dev/null 2>&1
  [ -n "$TMP" ] && [ -d "$TMP" ] && rm -rf "$TMP"
  case "$WR_STATE" in */artesanfc-qa-route-wrangler-*) [ -d "$WR_STATE" ] && rm -rf "$WR_STATE" ;; esac
  local left=""
  [ -n "$PG" ] && [ -n "$(docker ps -aq --filter "label=com.artesanfc.qa-private-route=$RUN_ID" 2>/dev/null)" ] && left="$left docker-container"
  [ -n "$TMP" ] && [ -e "$TMP" ] && left="$left temp-dir"
  [ -n "$WR_STATE" ] && [ -e "$WR_STATE" ] && left="$left wrangler-state"
  for p in "$QA_PID" "$FE_PID" "$API_PID"; do
    [ -n "$p" ] && kill -0 -- "-$p" 2>/dev/null && left="$left process-group-$p"
  done
  [ "$(marked_left)" != 0 ] && left="$left marked-processes"
  if [ -n "$left" ]; then printf 'cleanup: WARNING leftovers:%s\n' "$left" >&2; else say "cleanup: done (container, servers, browser and temp files removed)"; fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- helpers -------------------------------------------------------------------
free_port() { "$PYTHON" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

wait_http() { # url tries  -> 0 as soon as the URL answers with status < 500
  "$PYTHON" - "$1" "$2" <<'PY'
import sys, time, urllib.error, urllib.request
url, tries = sys.argv[1], int(sys.argv[2])
for _ in range(tries):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status < 500:
                sys.exit(0)
    except urllib.error.HTTPError as error:
        if error.code < 500:
            sys.exit(0)
    except Exception:
        pass
    time.sleep(0.5)
sys.exit(1)
PY
}

assert_group_leader() { # pid label
  local pgid
  pgid=$(ps -o pgid= -p "$1" 2>/dev/null | tr -d ' ')
  [ "$pgid" = "$1" ] || die "$2 did not start as its own process group (pid $1, pgid ${pgid:-none}); refusing to continue without safe cleanup"
}

# --- 0. configuration -----------------------------------------------------------
case "$QA_SERVER" in builtin|wrangler) ;; *) die "QA_SERVER must be 'builtin' or 'wrangler' (got '$QA_SERVER')" ;; esac
[ -d "$FRONTEND_DIR" ] || die "frontend directory not found: $FRONTEND_DIR"
FRONTEND_DIR=$(cd "$FRONTEND_DIR" && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/artesanfc-qa-route.XXXXXX")   # mode 0700
export no_proxy=127.0.0.1,localhost NO_PROXY=127.0.0.1,localhost
say "private-route QA  (server: $QA_SERVER, run: $RUN_ID)"

# --- 1. prerequisites (nothing is installed for you) -------------------------------
step "Checking prerequisites"
command -v docker >/dev/null 2>&1 || die "docker is required (disposable PostgreSQL). Install Docker and make sure your user can run it."
docker info >/dev/null 2>&1 || die "cannot talk to the Docker daemon (is it running, and is your user allowed to use it?)"
docker image inspect "$POSTGRES_IMAGE" >/dev/null 2>&1 \
  || die "Docker image $POSTGRES_IMAGE is not available locally. Pull it once:  docker pull $POSTGRES_IMAGE"
command -v "$PYTHON" >/dev/null 2>&1 || die "Python interpreter '$PYTHON' not found (set PYTHON=/path/to/python)"

"$PYTHON" - "$QA_DIR/private_route/requirements.txt" <<'PY' || die "missing Python prerequisites (see above and docs/QA_PRIVATE_ROUTE.md)"
import importlib.metadata, importlib.util, os, re, sys

problems = []
backend = {"fastapi": "fastapi", "uvicorn": "uvicorn", "sqlalchemy": "sqlalchemy", "alembic": "alembic",
           "psycopg": "psycopg[binary]", "pydantic_settings": "pydantic-settings"}
missing = [pkg for mod, pkg in backend.items() if importlib.util.find_spec(mod) is None]
if missing:
    problems.append("backend packages missing: " + ", ".join(missing) + "\n    fix: pip install -r backend/requirements.txt")

pin = re.search(r"^playwright==(\S+)", open(sys.argv[1]).read(), re.M).group(1)
try:
    installed = importlib.metadata.version("playwright")
except importlib.metadata.PackageNotFoundError:
    installed = None
if installed != pin:
    problems.append(f"Playwright {pin} is required, found {installed or 'none'}\n    fix: pip install -r qa/private_route/requirements.txt")
else:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if not os.path.exists(p.chromium.executable_path):
            problems.append("Playwright's Chromium browser is not installed\n    fix: python3 -m playwright install chromium")

for problem in problems:
    print("  - " + problem, file=sys.stderr)
sys.exit(1 if problems else 0)
PY
say "  python, backend packages, Playwright pin and Chromium: ok"

"$PYTHON" - "$API_HOST" "$API_PORT" <<'PY' || die "127.0.0.1:$API_PORT is already in use. This QA needs that port because frontend/assets/js/api-config.js maps the localhost/127.0.0.1 frontend to http://127.0.0.1:8000/api/v1 (and that file is deliberately not changed). Stop whatever listens there (find it with: ss -ltnp | grep :$API_PORT) and run again."
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # same as uvicorn: a TIME_WAIT leftover is not "in use"
try:
    s.bind((sys.argv[1], int(sys.argv[2])))
except OSError:
    sys.exit(1)
PY
say "  127.0.0.1:$API_PORT is free"

if [ "$QA_SERVER" = wrangler ]; then
  if [ -n "${WRANGLER_BIN:-}" ]; then
    WRANGLER_CMD=("$WRANGLER_BIN")
  elif command -v wrangler >/dev/null 2>&1; then
    WRANGLER_CMD=(wrangler)
  elif command -v npx >/dev/null 2>&1 && (cd "$TMP" && npx --no-install "wrangler@$WRANGLER_PIN" --version >/dev/null 2>&1); then
    WRANGLER_CMD=(npx --no-install "wrangler@$WRANGLER_PIN")
  else
    die "QA_SERVER=wrangler needs wrangler, and this script never installs anything. Install it once:  npm install -g wrangler@$WRANGLER_PIN   (or set WRANGLER_BIN=/path/to/wrangler)"
  fi
  say "  wrangler: $(cd "$TMP" && "${WRANGLER_CMD[@]}" --version 2>/dev/null | tail -n 1) (pinned reference: $WRANGLER_PIN)"

  # Wrangler records every request PATH (so /c/{token}) in a local SQLite store with no
  # off switch. Its state must therefore live on RAM-backed storage; there is NO disk
  # fallback and no scan exemption: if no suitable location exists this exits 2 before
  # anything is started. QA_WRANGLER_RAM_DIR overrides /dev/shm (also the test hook) and
  # is validated exactly like the default (must be a writable tmpfs/ramfs directory).
  WR_BASE=${QA_WRANGLER_RAM_DIR:-/dev/shm}
  WR_STATE="$WR_BASE/artesanfc-qa-route-wrangler-$RUN_ID"      # set first so cleanup always covers it
  if ! ram_err=$(cd "$QA_DIR" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$QA_DIR" "$PYTHON" -m private_route.ram_state create "$WR_BASE" "$RUN_ID" 2>&1 >/dev/null); then
    WR_STATE=""                                               # nothing was created; never remove a path we do not own
    die "${ram_err:-cannot prepare RAM-backed Wrangler state storage}"
  fi
  say "  wrangler state dir: RAM-backed, unique to this run, removed on exit ($WR_BASE)"
fi

# --- 2. static routing rules + harness self-tests (no services needed) ---------------
step "Static checks: harness self-tests and the real _redirects/_headers"
(cd "$QA_DIR" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$QA_DIR" "$PYTHON" -m private_route.static_checks "$FRONTEND_DIR") \
  || { printf '\nFAIL: routing rules or harness self-tests failed; nothing was started\n' >&2; exit 1; }

# --- 3. disposable PostgreSQL ---------------------------------------------------------
step "Starting disposable PostgreSQL ($POSTGRES_IMAGE, tmpfs, loopback, ephemeral port)"
PG="$PREFIX-db"
DB_PASSWORD=$("$PYTHON" -c 'import secrets; print(secrets.token_hex(16))')
POSTGRES_PASSWORD=$DB_PASSWORD docker run -d --rm --name "$PG" \
  --label "com.artesanfc.qa-private-route=$RUN_ID" \
  -e POSTGRES_USER=qa -e POSTGRES_PASSWORD -e POSTGRES_DB=artesanfc_test \
  --tmpfs /var/lib/postgresql/data:rw \
  -p 127.0.0.1::5432 "$POSTGRES_IMAGE" -c fsync=off >/dev/null || die "cannot start the PostgreSQL container"
DB_PORT=$(docker port "$PG" 5432/tcp | head -n 1 | sed 's/.*://')
[ -n "$DB_PORT" ] || die "cannot resolve the PostgreSQL port"
ready=0
for _ in $(seq 1 120); do
  if docker exec "$PG" pg_isready -h 127.0.0.1 -U qa -d artesanfc_test >/dev/null 2>&1; then ready=1; break; fi
  sleep 0.5
done
[ "$ready" = 1 ] || die "PostgreSQL did not become ready within 60s"

FE_PORT=$(free_port)
FRONTEND_URL="http://127.0.0.1:$FE_PORT"
API_ORIGIN="http://$API_HOST:$API_PORT"

# Backend environment for every child: the test guard (APP_ENV=test + a test-marked
# database on loopback) is what allows the seed and the fixtures to write.
export APP_ENV=test
export DATABASE_URL="postgresql://qa:$DB_PASSWORD@127.0.0.1:$DB_PORT/artesanfc_test"
export CORS_ALLOWED_ORIGINS="$FRONTEND_URL"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export PYTHONPATH="$QA_DIR:$BACKEND_DIR"

step "Applying migrations"
(cd "$BACKEND_DIR" && "$PYTHON" -m alembic upgrade head) >"$TMP/alembic.log" 2>&1 \
  || { tail -n 15 "$TMP/alembic.log" >&2; die "alembic upgrade head failed"; }
say "  alembic upgrade head: ok"

# --- 4. API and frontend -----------------------------------------------------------
step "Starting the real API on $API_ORIGIN"
( cd "$BACKEND_DIR" && exec setsid "$PYTHON" -m uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" ) >"$TMP/uvicorn.log" 2>&1 &
API_PID=$!
assert_group_leader "$API_PID" "uvicorn"
wait_http "$API_ORIGIN/health" 120 || { tail -n 15 "$TMP/uvicorn.log" >&2; die "the API did not become healthy within 60s"; }
say "  /health: ok"

step "Starting the $QA_SERVER frontend server on $FRONTEND_URL (serving $(basename "$FRONTEND_DIR")/)"
if [ "$QA_SERVER" = builtin ]; then
  ( cd "$QA_DIR" && exec setsid "$PYTHON" -m private_route.static_server --root "$FRONTEND_DIR" --port "$FE_PORT" ) >"$TMP/frontend.log" 2>&1 &
else
  # Run outside the repo (its root wrangler.toml is the legacy D1 Worker). Config and
  # logs go to the temp dir (scanned for tokens at the end); --log-level warn keeps
  # request lines out of its output. Its request-path store is in the RAM-backed
  # $WR_STATE verified in the prerequisites (never disk).
  mkdir -p "$TMP/wrangler-cwd" "$TMP/xdg" "$TMP/wrangler-logs"
  ( cd "$TMP/wrangler-cwd" && export XDG_CONFIG_HOME="$TMP/xdg" WRANGLER_LOG_PATH="$TMP/wrangler-logs" \
      WRANGLER_SEND_METRICS=false CI=1 NO_UPDATE_NOTIFIER=1 npm_config_update_notifier=false \
      && exec setsid "${WRANGLER_CMD[@]}" pages dev "$FRONTEND_DIR" --ip 127.0.0.1 --port "$FE_PORT" \
           --persist-to "$WR_STATE" --log-level warn ) >"$TMP/frontend.log" 2>&1 &
fi
FE_PID=$!
assert_group_leader "$FE_PID" "the frontend server"
wait_http "$FRONTEND_URL/" 240 || { tail -n 15 "$TMP/frontend.log" >&2; die "the frontend server did not answer within 120s"; }
say "  frontend answers"

# --- 5. the checks -------------------------------------------------------------------
step "Running checks (Chromium, headless)"
( cd "$QA_DIR" && TMPDIR="$TMP" exec setsid "$PYTHON" -m private_route.main \
    --frontend-url "$FRONTEND_URL" --frontend-dir "$FRONTEND_DIR" --api-origin "$API_ORIGIN" \
    --tmp-dir "$TMP" --server-kind "$QA_SERVER" ${WR_STATE:+--wrangler-state "$WR_STATE"} ) &
QA_PID=$!
assert_group_leader "$QA_PID" "the QA runner"
rc=0
wait "$QA_PID" || rc=$?
QA_PID=""

case "$rc" in
  0) printf '\nPASS: private route QA [%s] - every check passed\n' "$QA_SERVER" ;;
  1) printf '\nFAIL: private route QA [%s] found problems (see FAIL lines above)\n' "$QA_SERVER" >&2 ;;
  *) printf '\nERROR: the QA runner crashed (exit %s); see the sanitized error above\n' "$rc" >&2; rc=2 ;;
esac
exit "$rc"
