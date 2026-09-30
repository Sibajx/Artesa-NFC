#!/usr/bin/env bash
# Publishes the public site to PRODUCTION (Cloudflare Pages project
# "artesanfc-web" = https://artesanfc.com). Human gate: run it yourself, it
# asks you to type the domain before anything is uploaded (web/README.md,
# "Despliegue").
#
#   web/scripts/deploy-production.sh [--dry-run]            # web/ (Astro) from origin/main
#   web/scripts/deploy-production.sh --legacy-frontend       # ROLLBACK: frontend/ from origin/main
#
# Guards (any failure stops before the upload):
#   * the checkout is exactly origin/main and web/ (or frontend/) is clean;
#   * web/: npm ci + npm run verify (format, lint, typecheck, unit, build,
#     asset budgets) on that commit;
#   * the build must not carry staging/preview-only API hosts as its only
#     production mapping (api-config sanity) and must keep /c/* private headers;
#   * the current production deployment id is printed and saved to
#     web/.deploy-previous-production (git-ignored) so a rollback target is
#     always known.
#
# Rollback, fastest first:
#   1. Cloudflare dashboard -> Pages -> artesanfc-web -> Deployments -> the
#      previous production deployment -> "Rollback to this deployment".
#   2. web/scripts/deploy-production.sh --legacy-frontend   (re-uploads frontend/).
#
# Requirements: `wrangler login` (Pages write) or CLOUDFLARE_API_TOKEN +
# CLOUDFLARE_ACCOUNT_ID.
set -euo pipefail
PROJECT="artesanfc-web"
BRANCH="main"
DOMAIN="artesanfc.com"
WRANGLER="npx -y wrangler@4.135.0"

MODE="web"
DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    --legacy-frontend) MODE="legacy" ;;
    *) echo "unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."          # web/
REPO="$(git rev-parse --show-toplevel)"
SOURCE_DIR="web"
[[ "${MODE}" == "legacy" ]] && SOURCE_DIR="frontend"

if [[ -n "$(git -C "${REPO}" status --porcelain -- "${SOURCE_DIR}" web/scripts)" ]]; then
  echo "refusing: ${SOURCE_DIR}/ has uncommitted changes" >&2
  exit 2
fi
git -C "${REPO}" fetch -q origin "${BRANCH}"
HEAD_SHA="$(git -C "${REPO}" rev-parse HEAD)"
if [[ "${HEAD_SHA}" != "$(git -C "${REPO}" rev-parse "origin/${BRANCH}")" ]]; then
  echo "refusing: HEAD is not origin/${BRANCH} (git checkout --detach origin/${BRANCH} first)" >&2
  exit 2
fi

if [[ "${MODE}" == "web" ]]; then
  npm ci --no-audit --no-fund
  npm run verify
  DEPLOY_DIR="dist"
  # api-config sanity: production must map artesanfc.com to the production API.
  if ! grep -rqs "https://api.artesanfc.com/api/v1" dist/_astro; then
    echo "refusing: the build does not contain the production API base" >&2
    exit 2
  fi
  # /c/{token} must stay private (no-store, no-referrer), as in frontend/_headers.
  if ! awk '/^\/c\/\*/{f=1;next} /^[^ ]/{f=0} f' dist/_headers | grep -q "Referrer-Policy: no-referrer"; then
    echo "refusing: dist/_headers lost the /c/* private headers" >&2
    exit 2
  fi
  LABEL="web/ (Astro)"
else
  DEPLOY_DIR="${REPO}/frontend"
  LABEL="frontend/ (legacy site, rollback)"
fi

echo
echo "Current production deployments of ${PROJECT}:"
${WRANGLER} pages deployment list --project-name "${PROJECT}" --environment production 2>/dev/null | sed -n '1,6p' || true
PREVIOUS="$(${WRANGLER} pages deployment list --project-name "${PROJECT}" --environment production --json 2>/dev/null \
  | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{const a=JSON.parse(s);console.log((a[0]||{}).Id||(a[0]||{}).id||"")}catch{console.log("")}})')"
if [[ -n "${PREVIOUS}" ]]; then
  echo "${PREVIOUS}" > .deploy-previous-production
  echo "Rollback target (current production): ${PREVIOUS}  (saved to web/.deploy-previous-production)"
else
  echo "WARNING: could not read the current production deployment id; note it in the dashboard before continuing"
fi

echo
echo "About to publish ${LABEL} at ${HEAD_SHA:0:7} to ${PROJECT} = https://${DOMAIN} (PRODUCTION)."
if [[ "${DRY_RUN}" == "1" ]]; then
  echo "dry-run: everything checked, nothing uploaded"
  exit 0
fi
read -r -p "Type ${DOMAIN} to continue: " answer
if [[ "${answer}" != "${DOMAIN}" ]]; then
  echo "aborted: nothing was uploaded" >&2
  exit 3
fi

${WRANGLER} pages deploy "${DEPLOY_DIR}" \
  --project-name "${PROJECT}" \
  --branch "${BRANCH}" \
  --commit-hash "${HEAD_SHA}" \
  --commit-message "${LABEL} from ${BRANCH} ${HEAD_SHA:0:7}" \
  --commit-dirty=false

echo
echo "Published ${LABEL} ${HEAD_SHA:0:7} to https://${DOMAIN}"
echo "Check from a network without the FortiGate: /, /piezas/, one piece, /artesanos/, and an NFC /c/<token> link."
echo "Rollback: dashboard 'Rollback to this deployment' on ${PREVIOUS:-the previous production deployment}, or"
echo "          web/scripts/deploy-production.sh --legacy-frontend"
