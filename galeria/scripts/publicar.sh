#!/usr/bin/env bash
# Publica la galería en Cloudflare Pages (proyecto artesanfc-galeria).
#
# Pone ?v=<huella del contenido> en las URLs de CSS y JS de la copia que se
# sube, para que la caché de artesanfc.com nunca mezcle un HTML nuevo con una
# hoja de estilos vieja (pasó el 2026-11-01: la página salió sin estilos).
#
#   galeria/scripts/publicar.sh            # prerender + publicar
#   galeria/scripts/publicar.sh --seco     # arma la copia y dice dónde, sin subir
set -euo pipefail

aqui="$(cd "$(dirname "$0")/.." && pwd)"
node "$aqui/scripts/prerender.mjs"

D="$(mktemp -d)"
cp -r "$aqui/index.html" "$aqui/_headers" "$aqui/_redirects" "$aqui/assets" "$D"/
rm -f "$D/assets/credits.json"

huella() { sha256sum "$1" | cut -c1-10; }
v_css="$(huella "$D/assets/css/site.css")"
v_boot="$(huella "$D/assets/js/boot.js")"
v_render="$(huella "$D/assets/js/render.js")"

# app.js importa render.js: se versiona primero y luego se calcula la huella de app.js.
sed -i "s#from \"./render.js\"#from \"./render.js?v=$v_render\"#" "$D/assets/js/app.js"
v_app="$(huella "$D/assets/js/app.js")"

sed -i \
  -e "s#href=\"assets/css/site.css\"#href=\"assets/css/site.css?v=$v_css\"#" \
  -e "s#src=\"assets/js/boot.js\"#src=\"assets/js/boot.js?v=$v_boot\"#" \
  -e "s#src=\"assets/js/app.js\"#src=\"assets/js/app.js?v=$v_app\"#" \
  "$D/index.html"

for r in "site.css?v=$v_css" "boot.js?v=$v_boot" "app.js?v=$v_app"; do
  grep -q "$r" "$D/index.html" || { echo "No se versionó $r en index.html" >&2; exit 1; }
done
grep -q "render.js?v=$v_render" "$D/assets/js/app.js" || { echo "No se versionó render.js" >&2; exit 1; }

if [[ "${1:-}" == "--seco" ]]; then
  echo "Copia lista en $D"
  exit 0
fi

npx wrangler@4 pages deploy "$D" --project-name artesanfc-galeria --branch main --commit-dirty=true
