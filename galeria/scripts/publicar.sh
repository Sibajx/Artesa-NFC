#!/usr/bin/env bash
# Publica la galería en Cloudflare Pages (proyecto artesanfc-galeria).
#
# Todo se arma en una copia temporal (el repo no cambia):
#   1. scripts/fotos.py: copias WebP reducidas de las fotos de piezas y artesanos.
#   2. scripts/prerender.mjs: la copia estática de lo publicado en Gestión.
#   3. ?v=<huella> en CSS y JS, para que la caché de artesanfc.com nunca mezcle
#      un HTML nuevo con una hoja de estilos vieja (pasó el 2026-11-01: la
#      página salió sin estilos).
#
#   galeria/scripts/publicar.sh            # armar + publicar
#   galeria/scripts/publicar.sh --seco     # arma la copia y dice dónde, sin subir
set -euo pipefail

aqui="$(cd "$(dirname "$0")/.." && pwd)"
D="$(mktemp -d)"
cp -r "$aqui/index.html" "$aqui/_headers" "$aqui/_redirects" "$aqui/robots.txt" "$aqui/sitemap.xml" \
  "$aqui/assets" "$aqui/scripts" "$D"/
rm -f "$D/assets/credits.json"

python3 -I "$D/scripts/fotos.py" "$D"
node "$D/scripts/prerender.mjs"
rm -rf "$D/scripts"

huella() { sha256sum "$1" | cut -c1-10; }

# Cadena de imports: fotos.js ← render.js ← app.js ← index.html. Se versiona de
# la hoja hacia arriba para que cada huella incluya la de lo que importa.
v_fotos="$(huella "$D/assets/js/fotos.js")"
sed -i "s#from \"./fotos.js\"#from \"./fotos.js?v=$v_fotos\"#" "$D/assets/js/render.js"
v_render="$(huella "$D/assets/js/render.js")"
sed -i "s#from \"./render.js\"#from \"./render.js?v=$v_render\"#" "$D/assets/js/app.js"
v_app="$(huella "$D/assets/js/app.js")"
v_css="$(huella "$D/assets/css/site.css")"

sed -i \
  -e "s#href=\"assets/css/site.css\"#href=\"assets/css/site.css?v=$v_css\"#" \
  -e "s#src=\"assets/js/app.js\"#src=\"assets/js/app.js?v=$v_app\"#" \
  "$D/index.html"

for r in "site.css?v=$v_css" "app.js?v=$v_app"; do
  grep -q "$r" "$D/index.html" || { echo "No se versionó $r en index.html" >&2; exit 1; }
done
grep -q "render.js?v=$v_render" "$D/assets/js/app.js" || { echo "No se versionó render.js" >&2; exit 1; }
grep -q "fotos.js?v=$v_fotos" "$D/assets/js/render.js" || { echo "No se versionó fotos.js" >&2; exit 1; }

if [[ "${1:-}" == "--seco" ]]; then
  echo "Copia lista en $D"
  exit 0
fi

npx wrangler@4 pages deploy "$D" --project-name artesanfc-galeria --branch main --commit-dirty=true
