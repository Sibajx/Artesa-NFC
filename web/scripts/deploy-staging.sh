#!/usr/bin/env bash
# Deploys web/ to the STAGING Cloudflare Pages project (artesanfc-staging).
#
# DANGER — naming: the Pages project "artesanfc-web" is PRODUCTION
# (artesanfc.com, serving frontend/ by direct upload). This script only ever
# targets "artesanfc-staging" and refuses anything else. Production changes
# are a human gate (web/README.md, "Despliegue").
#
# Requirements: a clean checkout of origin/develop, `wrangler login` (or
# CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID with Pages:Edit).
# Usage: web/scripts/deploy-staging.sh
set -euo pipefail

PROJECT="artesanfc-staging"
BRANCH="develop"
WRANGLER="npx -y wrangler@4.135.0"

cd "$(dirname "$0")/.."

if [[ "${PROJECT}" != "artesanfc-staging" ]]; then
  echo "refusing: only artesanfc-staging may be deployed from here" >&2
  exit 2
fi
if [[ -n "$(git status --porcelain -- .)" ]]; then
  echo "refusing: web/ has uncommitted changes" >&2
  exit 2
fi
git fetch -q origin "${BRANCH}"
HEAD_SHA="$(git rev-parse HEAD)"
if [[ "${HEAD_SHA}" != "$(git rev-parse "origin/${BRANCH}")" ]]; then
  echo "refusing: HEAD is not origin/${BRANCH} (checkout it first)" >&2
  exit 2
fi

npm ci --no-audit --no-fund
npm run verify
${WRANGLER} pages deploy dist \
  --project-name "${PROJECT}" \
  --branch "${BRANCH}" \
  --commit-hash "${HEAD_SHA}" \
  --commit-message "web/ from ${BRANCH} ${HEAD_SHA:0:7}" \
  --commit-dirty=false
echo "Deployed ${HEAD_SHA:0:7} to ${PROJECT} (https://staging.artesanfc.com)"
