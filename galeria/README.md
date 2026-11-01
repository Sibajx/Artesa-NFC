# Galería — artesanfc.com

Sitio estático (Cloudflare Pages, proyecto `artesanfc-galeria`, subida directa)
conectado a Gestión por la API pública `https://api.artesanfc.com/api/v1`.

## Cómo se conecta con Gestión

- Muestra las piezas **publicadas** en Gestión que tienen foto, su paleta
  (`visual_theme.palette`, cada pieza pinta su propio capítulo), sus artesanos
  y la imagen de sitio `collection-entry` como portada.
- `index.html` lleva una copia prerenderizada (regiones `<!-- live:* -->`) para
  buscadores y para quien no tiene JS. Al abrir la página, `assets/js/app.js`
  vuelve a pedir la API y repinta solo si algo cambió: lo que se publica o
  despublica en Gestión se ve sin volver a desplegar.
- La API solo acepta CORS desde `https://artesanfc.com`; en `*.pages.dev` la
  página usa la copia prerenderizada.
- El verificador de códigos busca `public_code` en `/pieces`; el certificado y
  la ficha completa viven en `info.artesanfc.com` (los enlaces viejos
  `/c/*`, `/piezas/*`, etc. redirigen allí con `_redirects`).

## Publicar

```bash
node galeria/scripts/prerender.mjs          # refresca la copia estática
D=$(mktemp -d) && cp -r galeria/index.html galeria/_headers galeria/_redirects galeria/assets "$D"/ \
  && rm -f "$D/assets/credits.json" \
  && npx wrangler@4 pages deploy "$D" --project-name artesanfc-galeria --branch main
```

Volver atrás: Cloudflare → Pages → `artesanfc-galeria` → Deployments → la
anterior → *Rollback*.

## Archivos

| Archivo | Qué hace |
|---|---|
| `assets/js/render.js` | Datos de la API → HTML. Escapa todo texto, valida URLs `/media/` y colores `#rrggbb`. Lo usan el navegador y el prerender. |
| `assets/js/app.js` | Capa viva: refresco desde la API, verificador, acordeón, barra con el color del capítulo, entradas. |
| `assets/js/boot.js` | Marca `html.js` antes de pintar (sin scripts en línea por la CSP). |
| `scripts/prerender.mjs` | Llena las regiones `live:*` de `index.html` con lo publicado hoy. |
| `_headers` | CSP y caché. |
| `assets/credits.json` | Créditos de las fotos abiertas (Wikimedia Commons); el pie de página los cita. |
