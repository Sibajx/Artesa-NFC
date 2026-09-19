#!/usr/bin/env bash
# Manual, Docker-based check that a literal /c/{token} request reaching the API
# host never leaves the token in any Nginx or Uvicorn log (docs/SECURITY.md
# sections 7 and 13). Not part of CI or pytest.
#
# It runs the REAL artesanfc-api.conf.example (unmodified) in front of a real
# Uvicorn built from backend/Dockerfile, sends canary requests over HTTP and
# HTTPS, and greps every log for the canaries. Two phases:
#
#   1. FULL      the conf as shipped. /c* must be a local 404, unlogged and
#                never proxied; /api/v1 must still work (routing, rate
#                limiting, headers, normal access logging).
#   2. FALLBACK  the same conf with the private-route locations removed, to
#                prove the log-format map alone keeps the token out of the
#                access log if a location is ever missed or changed.
#
# Requirements: docker, curl, openssl, python3, timeout. Needs no network
# beyond pulling the images if they are not already local.
#
# Everything is created under a per-run unique name and removed on exit
# (also on INT/TERM): containers, a private Docker network, a dedicated image
# tag and a temp dir. Nothing else is touched. Host ports are ephemeral and
# bound to 127.0.0.1 only. Set KEEP_IMAGE=1 to keep the built image (its tag
# is printed) for faster reruns; NGINX_IMAGE overrides nginx:stable-alpine.
#
# Exit status: 0 = every check passed, non-zero = a privacy/behaviour failure
# (or the setup itself failed).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BACKEND_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
CONF_SRC=${CONF_SRC:-$SCRIPT_DIR/artesanfc-api.conf.example}   # override only to test a variant conf
NGINX_IMAGE=${NGINX_IMAGE:-nginx:stable-alpine}
HOST_NAME=api.artesanfc.com

RUN_ID="$(date +%s)-$$"
PREFIX="artesanfc-logpriv-$RUN_ID"
NET="$PREFIX-net"
API="$PREFIX-api"
IMAGE="artesanfc-logpriv-api:$RUN_ID"

# Canaries. Distinct from the ordinary-API markers on purpose: Uvicorn logs the
# query string of /api/v1 requests, and that must not be confused with a leak.
TOKEN=PRIVATE_CANARY_TOKEN
QUERY=QUERY_CANARY
LEAK_RE='PRIVATE_CANARY|QUERY_CANARY'
API_MARK=API_QUERY_MARKER
ORD_MARK=ORDINARY_MARKER

TMP=""
CONTAINERS=()
NET_CREATED=0
IMAGE_BUILT=0
PASS=0
FAILS=0
UV_EXPECTED=0
HTTPS_PORT=""
HTTP_PORT=""
NGX=""
LOGS=""

cleanup() {
  local rc=$?
  set +e
  local c
  for c in "${CONTAINERS[@]:-}"; do
    [ -n "$c" ] && docker rm -f "$c" >/dev/null 2>&1
  done
  [ "$NET_CREATED" = 1 ] && docker network rm "$NET" >/dev/null 2>&1
  if [ "$IMAGE_BUILT" = 1 ]; then
    if [ "${KEEP_IMAGE:-0}" = 1 ]; then
      echo "kept image $IMAGE"
    else
      docker rmi "$IMAGE" >/dev/null 2>&1
    fi
  fi
  [ -n "$TMP" ] && [ -d "$TMP" ] && rm -rf "$TMP"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ok()   { PASS=$((PASS + 1)); printf '  PASS  %s\n' "$*"; }
bad()  { FAILS=$((FAILS + 1)); printf '  FAIL  %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 2; }
section() { printf '\n== %s ==\n' "$*"; }

expect_eq() { # description actual expected
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', expected '$3')"; fi
}

# expect_no_leak <description> <file>...  - none of the canaries in any file.
expect_no_leak() {
  local desc=$1; shift
  local f hits=0
  for f in "$@"; do
    if [ ! -r "$f" ]; then bad "$desc: cannot read $f"; return; fi
    if grep -Eq "$LEAK_RE" "$f"; then
      hits=1
      printf '        leak in %s:\n' "$(basename "$f")"
      grep -E "$LEAK_RE" "$f" | cut -c1-160 | head -n 5 | sed 's/^/          /'
    fi
  done
  if [ "$hits" = 0 ]; then ok "$desc"; else bad "$desc"; fi
}

# --- prerequisites -----------------------------------------------------------
for tool in docker curl openssl python3 timeout; do
  command -v "$tool" >/dev/null 2>&1 || die "missing required tool: $tool"
done
docker info >/dev/null 2>&1 || die "cannot talk to the Docker daemon"
[ -r "$CONF_SRC" ] || die "missing $CONF_SRC"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/artesanfc-logpriv.XXXXXX")
mkdir "$TMP/certs"
echo "run id: $RUN_ID (all resources are prefixed $PREFIX)"

# --- static checks on the conf ------------------------------------------------
section "Static checks: $(basename "$CONF_SRC")"
expect_eq "private-route block present in both servers (:80 and :443)" \
  "$(grep -c '# BEGIN private-certificate-route' "$CONF_SRC")" 2
expect_eq "both servers set an explicit ArtesaNFC access_log" \
  "$(grep -c '^ *access_log /var/log/nginx/artesanfc-api.access.log artesanfc_api;' "$CONF_SRC")" 2
FMT_LINE=$(grep -E '^log_format artesanfc_api' "$CONF_SRC" || true)
if [ -n "$FMT_LINE" ] && ! printf '%s' "$FMT_LINE" | grep -Eq '\$request_uri|\$request([^_[:alnum:]]|$)|\$args|\$query_string|\$is_args|\$uri([^_[:alnum:]]|$)'; then
  ok "log_format artesanfc_api uses none of \$request, \$request_uri, query variables or raw \$uri"
else
  bad "log_format artesanfc_api must not use \$request, \$request_uri, query variables or raw \$uri: $FMT_LINE"
fi

# --- build / start the real stack ---------------------------------------------
section "Setup"
if ! docker image inspect "$NGINX_IMAGE" >/dev/null 2>&1; then
  timeout 300 docker pull -q "$NGINX_IMAGE" >/dev/null || die "cannot pull $NGINX_IMAGE"
fi
timeout 900 docker build -q -t "$IMAGE" \
  --label "com.artesanfc.log-privacy-validation=$RUN_ID" "$BACKEND_DIR" >/dev/null \
  || die "docker build failed"
IMAGE_BUILT=1
docker network create --label "com.artesanfc.log-privacy-validation=$RUN_ID" "$NET" >/dev/null \
  || die "cannot create network"
NET_CREATED=1

openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=$HOST_NAME" \
  -keyout "$TMP/certs/privkey.pem" -out "$TMP/certs/fullchain.pem" 2>/dev/null \
  || die "cannot generate the self-signed test certificate"

# Uvicorn: the image's own CMD (uvicorn app.main:app --host 0.0.0.0 --port 8000).
# Nginx joins its network namespace so the conf's `server 127.0.0.1:8000`
# upstream and its certificate paths are used verbatim. The published ports
# belong to this container, so they survive swapping the Nginx container.
CONTAINERS+=("$API")
docker run -d --name "$API" --network "$NET" \
  -p 127.0.0.1::443 -p 127.0.0.1::80 \
  -e APP_ENV=local -e DATABASE_URL=postgresql://nobody:nobody@127.0.0.1:1/none \
  "$IMAGE" >/dev/null || die "cannot start the API container"
HTTPS_PORT=$(docker port "$API" 443/tcp | head -n 1 | sed 's/.*://')
HTTP_PORT=$(docker port "$API" 80/tcp | head -n 1 | sed 's/.*://')
[ -n "$HTTPS_PORT" ] && [ -n "$HTTP_PORT" ] || die "cannot resolve the published ports"

started=0
for _ in $(seq 1 60); do
  if docker logs "$API" 2>&1 | grep -q 'Application startup complete'; then started=1; break; fi
  sleep 0.5
done
[ "$started" = 1 ] || die "Uvicorn did not start within 30s"
ok "Uvicorn up (real container, image CMD)"

# start_nginx <label> <conf-file>: syntax-check, then run Nginx in front of Uvicorn.
start_nginx() {
  local label=$1 conf=$2
  local dir="$TMP/$label"
  mkdir "$dir" "$dir/conf.d" "$dir/logs"
  chmod 777 "$dir/logs"            # the worker user creates the log files here
  cp "$conf" "$dir/conf.d/artesanfc-api.conf"
  LOGS="$dir/logs"
  local mounts=(-v "$dir/conf.d:/etc/nginx/conf.d:ro"
                -v "$TMP/certs:/etc/letsencrypt/live/$HOST_NAME:ro"
                -v "$dir/logs:/var/log/nginx")

  local tname="$PREFIX-nginxt-$label"
  CONTAINERS+=("$tname")
  if out=$(docker run --rm --name "$tname" "${mounts[@]}" "$NGINX_IMAGE" nginx -t 2>&1); then
    ok "nginx -t ($label): $(printf '%s' "$out" | tail -n 1)"
  else
    printf '%s\n' "$out" | sed 's/^/        /'
    bad "nginx -t ($label) failed"; return 1
  fi

  NGX="$PREFIX-nginx-$label"
  CONTAINERS+=("$NGX")
  docker run -d --name "$NGX" --network "container:$API" "${mounts[@]}" "$NGINX_IMAGE" >/dev/null \
    || die "cannot start Nginx ($label)"
  local up=0
  for _ in $(seq 1 60); do
    if [ "$(curl -sk --noproxy '*' --max-time 3 --resolve "$HOST_NAME:$HTTPS_PORT:127.0.0.1" \
          -o /dev/null -w '%{http_code}' "https://$HOST_NAME:$HTTPS_PORT/" 2>/dev/null || true)" = 404 ]; then
      up=1; break
    fi
    sleep 0.5
  done
  [ "$up" = 1 ] || die "Nginx ($label) did not come up within 30s"
}

# --- request helpers -----------------------------------------------------------
# req <scheme> <method> <target> [curl args...]  -> R_STATUS, R_HEAD, R_BODY.
# --path-as-is keeps //c, /./c and /x/../c intact so Nginx does the normalizing.
R_STATUS=""; R_HEAD="$TMP/resp.head"; R_BODY="$TMP/resp.body"
req() {
  local scheme=$1 method=$2 target=$3; shift 3
  : >"$R_HEAD"; : >"$R_BODY"
  if [ "$scheme" = https ]; then
    R_STATUS=$(curl -sk --noproxy '*' --max-time 10 --path-as-is \
      --resolve "$HOST_NAME:$HTTPS_PORT:127.0.0.1" -X "$method" -D "$R_HEAD" -o "$R_BODY" \
      -w '%{http_code}' "$@" "https://$HOST_NAME:$HTTPS_PORT$target" 2>/dev/null) || R_STATUS=000
  else
    R_STATUS=$(curl -s --noproxy '*' --max-time 10 --path-as-is -H "Host: $HOST_NAME" \
      -X "$method" -D "$R_HEAD" -o "$R_BODY" \
      -w '%{http_code}' "$@" "http://127.0.0.1:$HTTP_PORT$target" 2>/dev/null) || R_STATUS=000
  fi
}
header_count() { grep -ci "^$1:" "$R_HEAD" || true; }
header_value() { grep -i "^$1:" "$R_HEAD" | head -n 1 | cut -d: -f2- | tr -d '\r' | sed 's/^ *//'; }

# Malformed / parser-level requests that Nginx may log before choosing a location.
cat >"$TMP/raw.py" <<'PY'
import socket, ssl, sys
scheme, port, case = sys.argv[1], int(sys.argv[2]), sys.argv[3]
T = "/c/PRIVATE_CANARY_TOKEN"
H = "Host: api.artesanfc.com\r\nConnection: close\r\n\r\n"
reqs = {
    "control-character": "GET " + T + "\x01X HTTP/1.1\r\n" + H,
    "long-uri-414":      "GET " + T + "A" * 10000 + " HTTP/1.1\r\n" + H,
    "malformed-method":  "BOGUS " + T + " HTTP/1.1\r\n" + H,
    "unknown-host":      "GET " + T + "?x=QUERY_CANARY HTTP/1.1\r\nHost: evil.example\r\nConnection: close\r\n\r\n",
}
s = socket.create_connection(("127.0.0.1", port), timeout=5)
if scheme == "https":
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    s = ctx.wrap_socket(s, server_hostname="api.artesanfc.com")
try:
    s.sendall(reqs[case].encode("latin-1"))
except OSError:
    pass  # Nginx may answer 414/400 and close before the whole request is sent
data = b""
try:
    while len(data) < 65536:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
except OSError:
    pass
s.close()
text = data.decode("latin-1")
status = text.split(" ", 2)[1] if text.startswith("HTTP/") else "000"
leak = 1 if ("CANARY" in text) else 0
print(status, leak)
PY
RAW_CASES=(control-character long-uri-414 malformed-method unknown-host)
raw_case() { # scheme case -> R_STATUS, R_LEAK
  local port=$HTTP_PORT
  [ "$1" = https ] && port=$HTTPS_PORT
  local out
  out=$(timeout 20 python3 "$TMP/raw.py" "$1" "$port" "$2" 2>/dev/null) || out="000 0"
  R_STATUS=${out%% *}; R_LEAK=${out##* }
}

# The private namespace matrix. T = the private canary token.
# path|extra curl args (POST needs a body).
PRIVATE_TARGETS=(
  "/c"
  "/c/"
  "/c/$TOKEN"
  "/C"
  "/C/"
  "/C/$TOKEN"
  "/c/$TOKEN?x=$QUERY"
  "//c/$TOKEN"
  "/./c/$TOKEN"
  "/x/../c/$TOKEN"
  "/%63/$TOKEN"
  "/%43/$TOKEN"
  "/c%2F$TOKEN"
  "/c/$TOKEN/"
  "/c//$TOKEN"
)

# --- PHASE 1: the conf as shipped ---------------------------------------------
section "Phase 1 - FULL: conf as shipped"
start_nginx full "$CONF_SRC" || die "cannot continue without a valid conf"

echo "-- private certificate namespace (HTTP and HTTPS): local 404, no redirect, no echo"
for scheme in https http; do
  for target in "${PRIVATE_TARGETS[@]}"; do
    req "$scheme" GET "$target"
    loc=$(header_count location)
    if [ "$R_STATUS" = 404 ] && [ "$loc" = 0 ] \
       && ! grep -Eqi "$LEAK_RE" "$R_HEAD" "$R_BODY"; then
      ok "[$scheme] GET  $target -> 404, no Location, token/query not echoed"
    else
      bad "[$scheme] GET  $target -> status=$R_STATUS Location-headers=$loc"
    fi
  done
  req "$scheme" POST "/c/$TOKEN" -d "x=$QUERY"
  if [ "$R_STATUS" = 404 ] && [ "$(header_count location)" = 0 ] \
     && ! grep -Eqi "$LEAK_RE" "$R_HEAD" "$R_BODY"; then
    ok "[$scheme] POST /c/$TOKEN -> 404, no Location, no echo"
  else
    bad "[$scheme] POST /c/$TOKEN -> status=$R_STATUS"
  fi
done

echo "-- malformed / parser-level requests (HTTP and HTTPS)"
for scheme in https http; do
  for c in "${RAW_CASES[@]}"; do
    raw_case "$scheme" "$c"
    if [[ "$R_STATUS" =~ ^4[0-9][0-9]$ ]] && [ "$R_LEAK" = 0 ]; then
      ok "[$scheme] $c -> $R_STATUS, no redirect, token/query not echoed"
    else
      bad "[$scheme] $c -> status=$R_STATUS leak-in-response=$R_LEAK"
    fi
  done
done

echo "-- ordinary API and non-private requests"
req https GET "/api/v1/nonexistent?q=$API_MARK"
expect_eq "[https] GET /api/v1/nonexistent -> 404 from the API" "$R_STATUS" 404
UV_EXPECTED=$((UV_EXPECTED + 1))

req https POST /api/v1/certificates/resolve -H 'Content-Type: application/json' -d '{}'
expect_eq "[https] POST /api/v1/certificates/resolve {} -> 422 from the API" "$R_STATUS" 422
expect_eq "    resolve response has exactly one Cache-Control header" "$(header_count cache-control)" 1
expect_eq "    resolve Cache-Control is no-store" "$(header_value cache-control)" no-store
UV_EXPECTED=$((UV_EXPECTED + 1))

req https GET "/unknown/ordinary?y=$ORD_MARK"
expect_eq "[https] GET /unknown/ordinary -> 404 (Nginx, not proxied)" "$R_STATUS" 404
req https GET /health
expect_eq "[https] GET /health -> 404 (not exposed)" "$R_STATUS" 404

req http GET "/api/v1/things?q=$ORD_MARK"
expect_eq "[http]  GET /api/v1/things -> 301 (ordinary redirect kept)" "$R_STATUS" 301
expect_eq "    redirect target is https://$HOST_NAME/..." \
  "$(header_value location | cut -c1-$((${#HOST_NAME} + 9)))" "https://$HOST_NAME/"
req http GET /unknown/ordinary
expect_eq "[http]  GET /unknown/ordinary -> 301 (ordinary redirect kept)" "$R_STATUS" 301

echo "-- resolve rate limiting (30r/m, burst=5)"
n429=0; n422=0; other=0; first429_head=""; first429_body=""
for _ in $(seq 1 14); do
  req https POST /api/v1/certificates/resolve -H 'Content-Type: application/json' -d '{}'
  case "$R_STATUS" in
    429) n429=$((n429 + 1))
         if [ -z "$first429_head" ]; then first429_head=$(cat "$R_HEAD"); first429_body=$(cat "$R_BODY"); fi ;;
    422) n422=$((n422 + 1)) ;;
    *)   other=$((other + 1)) ;;
  esac
done
UV_EXPECTED=$((UV_EXPECTED + n422))
if [ "$n429" -ge 1 ] && [ "$n422" -ge 5 ] && [ "$other" = 0 ]; then
  ok "burst of 14 resolve POSTs: $n422 reached the API (422), $n429 rate-limited (429)"
else
  bad "burst of 14 resolve POSTs: 422=$n422 429=$n429 other=$other"
fi
printf '%s' "$first429_head" | grep -qi '^retry-after: 2' && ok "    429 carries Retry-After: 2" || bad "    429 lacks Retry-After: 2"
printf '%s' "$first429_head" | grep -qi '^cache-control: no-store' && ok "    429 carries Cache-Control: no-store" || bad "    429 lacks Cache-Control: no-store"
printf '%s' "$first429_body" | grep -q '"code":"rate_limited"' && ok "    429 body is the rate_limited envelope" || bad "    429 body is not the rate_limited envelope"

sleep 1   # let Nginx and Uvicorn flush their logs
docker logs "$API" >"$TMP/uvicorn.out" 2>"$TMP/uvicorn.err" || true
docker logs "$NGX" >"$TMP/nginx.out" 2>"$TMP/nginx.err" || true
UV_LINES_FULL=$(cat "$TMP/uvicorn.out" "$TMP/uvicorn.err" | wc -l | tr -d ' ')
API_LOG="$LOGS/artesanfc-api.access.log"

echo "-- Nginx access log (artesanfc-api.access.log)"
expect_no_leak "no private token or query canary in any Nginx access log (ArtesaNFC and stock)" "$API_LOG" "$LOGS/access.log"
expect_eq "no /c path logged at all (access_log off; no redacted entries either)" \
  "$(grep -Ec '"[A-Z-]+ /[cC]([/" ]|$)' "$API_LOG" || true)" 0
expect_eq "no query string / ordinary query marker in the access log" \
  "$(grep -Ec "\?|$API_MARK|$ORD_MARK" "$API_LOG" || true)" 0
expect_eq "stock http-level access.log receives nothing from these servers" \
  "$(wc -c <"$LOGS/access.log" | tr -d ' ')" 0

echo "-- ordinary API access logging still works"
for pat in '"GET /api/v1/nonexistent" 404' '"POST /api/v1/certificates/resolve" 422' \
           '"POST /api/v1/certificates/resolve" 429' '"GET /unknown/ordinary" 404' \
           '"GET /health" 404' '"GET /api/v1/things" 301' '"GET /unknown/ordinary" 301'; do
  if grep -Fq "$pat" "$API_LOG"; then ok "logged: $pat"; else bad "missing log line: $pat"; fi
done

echo "-- Nginx error log and container output"
expect_no_leak "no canary in Nginx error.log" "$LOGS/error.log"
expect_no_leak "no canary in Nginx container stdout/stderr" "$TMP/nginx.out" "$TMP/nginx.err"

echo "-- Uvicorn"
expect_no_leak "no canary in Uvicorn stdout/stderr" "$TMP/uvicorn.out" "$TMP/uvicorn.err"
expect_eq "Uvicorn saw exactly the $UV_EXPECTED /api/v1 requests sent (no /c*, nothing else)" \
  "$(cat "$TMP/uvicorn.out" "$TMP/uvicorn.err" | grep -Ec '"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) [^"]* HTTP/[0-9.]+"')" "$UV_EXPECTED"
expect_eq "no /c or /C request line in Uvicorn" \
  "$(cat "$TMP/uvicorn.out" "$TMP/uvicorn.err" | grep -Ec '"[A-Z]+ /[cC]([/? "]|%)' || true)" 0
if cat "$TMP/uvicorn.out" "$TMP/uvicorn.err" | grep -q "$API_MARK"; then
  ok "ordinary /api/v1 request reached Uvicorn (its log shows the API marker)"
else
  bad "the /api/v1 marker request never reached Uvicorn"
fi

# --- PHASE 2: log-format map as the only protection ----------------------------
section "Phase 2 - FALLBACK: private-route locations removed, map only"
sed '/# BEGIN private-certificate-route/,/# END private-certificate-route/d' "$CONF_SRC" >"$TMP/conf-fallback"
expect_eq "fallback conf has no /c location left" \
  "$(grep -c 'location ~\* \^/c' "$TMP/conf-fallback" || true)" 0
docker rm -f "$NGX" >/dev/null 2>&1 || true
start_nginx fallback "$TMP/conf-fallback" || die "cannot continue without a valid conf"

for scheme in https http; do
  for target in "${PRIVATE_TARGETS[@]}"; do req "$scheme" GET "$target"; done
  req "$scheme" POST "/c/$TOKEN" -d "x=$QUERY"
  for c in "${RAW_CASES[@]}"; do raw_case "$scheme" "$c"; done
done
sleep 1
docker logs "$API" >"$TMP/uvicorn2.out" 2>"$TMP/uvicorn2.err" || true
docker logs "$NGX" >"$TMP/nginx2.out" 2>"$TMP/nginx2.err" || true
API_LOG="$LOGS/artesanfc-api.access.log"

expect_no_leak "map only: no canary in any Nginx access log (ArtesaNFC and stock)" "$API_LOG" "$LOGS/access.log"
if grep -Fq '"GET /c/[redacted]"' "$API_LOG"; then
  ok "map only: /c paths are logged as the fixed placeholder /c/[redacted]"
else
  bad "map only: expected /c/[redacted] entries in the access log"
fi
expect_eq "map only: stock http-level access.log receives nothing" \
  "$(wc -c <"$LOGS/access.log" | tr -d ' ')" 0
expect_no_leak "map only: no canary in Nginx error.log" "$LOGS/error.log"
expect_no_leak "map only: no canary in Nginx container output" "$TMP/nginx2.out" "$TMP/nginx2.err"
expect_no_leak "map only: no canary in Uvicorn stdout/stderr" "$TMP/uvicorn2.out" "$TMP/uvicorn2.err"
expect_eq "map only: Uvicorn saw no new request" \
  "$(cat "$TMP/uvicorn2.out" "$TMP/uvicorn2.err" | wc -l | tr -d ' ')" "$UV_LINES_FULL"

# --- result --------------------------------------------------------------------
section "Result"
printf 'passed: %d   failed: %d\n' "$PASS" "$FAILS"
if [ "$FAILS" -gt 0 ]; then
  echo "FAIL: log-privacy validation found $FAILS problem(s)"
  exit 1
fi
echo "PASS: no private-route canary in any Nginx or Uvicorn log; /api/v1 unaffected"
