# ArtesaNFC — frontend público (Astro)

Destino de la migración incremental del frontend público. **`frontend/` sigue
siendo el sitio de producción** y queda intacto como referencia, fallback y
rollback. Esta app no cambia el backend, la API, el modelo de datos ni la
seguridad: consume `docs/API_CONTRACT.md` tal como está.

Decisión: `docs/DECISIONS.md` ADR-028.

## Arquitectura

| Pieza          | Elección                                            | Motivo                                                                                 |
| -------------- | --------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Framework      | Astro 7, salida **estática** (`dist/`)              | Mismo modelo de despliegue que `frontend/` (Cloudflare Pages, sin runtime de servidor) |
| Lenguaje       | TypeScript `strictest`                              |                                                                                        |
| Interactividad | React 19 **solo en islas** (`client:only`)          | La home no carga React (≈1 KB de JS inline)                                            |
| 3D             | `@google/model-viewer` cargado **bajo demanda**     | Three.js solo llega en un chunk diferido tras "Ver en 3D"                              |
| Datos          | API pública (`/api/v1`) como única fuente de verdad | ADR-019, F-08: nada de artesanos/piezas/certificados en el bundle                      |
| Fuentes        | Self-hosted (`@fontsource`)                         | Sin peticiones a terceros; `/c/` tampoco las hace                                      |

```text
src/
├── lib/            capa contractual: api-config.ts (base por hostname), api.ts
│                   (cliente tipado), types.ts, routes.ts, format.ts, media.ts, seo.ts
├── content/site.ts textos de la home y manifiesto de media (con bandera `provisional`)
├── layouts/        BaseLayout (head, header, footer, fuentes)
├── components/
│   ├── home/       Hero, Manifesto, CollectionEntry, Closing (Astro, sin JS de framework)
│   ├── ui/         SiteHeader, SiteFooter, ResponsivePicture, NoScript
│   └── islands/    PieceGallery, PieceDetail, PieceViewer, PassportPanel,
│                   ArtisanList, ArtisanDetail, CertificateView (+ estados compartidos)
├── pages/          /, /piezas/, /artesanos/, /c/, /shell/pieza/, /shell/artesano/, 404
├── scripts/site.ts header dinámico y reveal (mejora progresiva)
└── styles/         global.css (tokens), islands.css
public/             _redirects, _headers, robots.txt, favicon, media/placeholders/
```

### Rutas

| Ruta                               | Sirve                       | Notas                                                                                          |
| ---------------------------------- | --------------------------- | ---------------------------------------------------------------------------------------------- |
| `/`                                | `index.html`                | 4 bloques: hero de video, manifiesto, entrada a la colección, cierre. Sin API, funciona sin JS |
| `/piezas/`                         | lista                       | Fotos primero; badge "3D" y "Ver en 3D" (diálogo) solo si la pieza tiene `model_3d`            |
| `/piezas/{slug}`                   | `/shell/pieza/` (rewrite)   | Shell neutro F-08: ficha + pasaporte digital solo tras un `200`                                |
| `/artesanos/`, `/artesanos/{slug}` | lista / `/shell/artesano/`  | Igual que piezas                                                                               |
| `/c/{token}`                       | `/c/` (rewrite)             | Ruta privada existente (Issue #72); no se creó otra                                            |
| otra                               | `404.html` con **HTTP 404** | Antes: fallback SPA a la home                                                                  |

Los shells viven en `/shell/` (Astro ignora carpetas con `_`, por eso no es
`/_shell/` como en `frontend/`); siguen fuera de `/piezas/` y `/artesanos/`.

### Estados que distingue la UI

| Situación                                    | Pieza / artesano                                  | Certificado `/c/{token}`                                                                                   |
| -------------------------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Datos válidos                                | Ficha + "Pieza registrada en el catálogo público" | "Certificado válido" + pasaporte con versión, emisión y notas                                              |
| No existe / no publicado                     | "Pieza no disponible" (`404`, noindex)            | —                                                                                                          |
| Token desconocido, **revocado** o malformado | —                                                 | "No podemos confirmar este certificado" (un solo estado: el contrato §7/§13 prohíbe distinguir _revocado_) |
| 5xx, 429, timeout, red                       | "No pudimos cargar…" reintentable, con el motivo  | "Verificación no disponible por ahora" reintentable (nunca se confunde con _unavailable_)                  |
| Host sin API configurada                     | Aviso sin petición de red                         | Error de servicio sin petición                                                                             |

La ficha pública **no** revela si existe certificado o tag NFC
(`API_CONTRACT.md` §5): el texto de autenticidad es idéntico para todas las piezas.

### Variables de entorno

**Ninguna.** La base de la API se elige en tiempo de ejecución por hostname
exacto en `src/lib/api-config.ts` (misma tabla que
`frontend/assets/js/api-config.js`): `localhost`/`127.0.0.1` → API local,
`artesanfc.com` → `https://api.artesanfc.com/api/v1`, cualquier otro → sin API.
No hay override por build a propósito. Habilitar un staging o `www` = añadir
el hostname ahí **y** en `CORS_ALLOWED_ORIGINS` del backend.

## Comandos

```bash
cd web
npm ci
npm run dev            # http://localhost:4321 (API local en 127.0.0.1:8000)
npm run build          # dist/
npm run verify         # format + lint + typecheck + unit + build + presupuestos
npm run test:e2e       # Playwright (desktop + Pixel 7) sobre dist/, API interceptada
node scripts/serve-dist.mjs --port 4330   # dist/ con reglas de Pages (_redirects/_headers/404)
npm run lighthouse -- / /piezas/          # con serve-dist en :4330
npx wrangler@4.135.0 pages dev dist       # verificación de reglas con el emulador de Pages
```

### Validación contra la API real (opcional)

```bash
# backend local sembrado (backend/README.md), con CORS para el origen del e2e:
CORS_ALLOWED_ORIGINS=http://localhost:4329,http://localhost:4330 uvicorn app.main:app --port 8000
LIVE_API=1 npx playwright test tests/e2e/live-api.spec.ts
# LIVE_TOKEN / LIVE_REVOKED_TOKEN: tokens emitidos en una base DESECHABLE; nunca se versionan.
```

## Despliegue (gate humano)

Nada de esta app está desplegado. Producción sigue sirviendo `frontend/`.

1. **Staging** (requisito antes de producción): proyecto de Cloudflare Pages
   separado (p. ej. `artesanfc-web`, gratuito) o preview del proyecto actual con
   un hostname propio (p. ej. `staging.artesanfc.com`).
   Build: _root_ `web`, comando `npm ci && npm run build`, salida `dist`,
   `NODE_VERSION=22`. Para que tenga datos: añadir ese hostname a
   `src/lib/api-config.ts` y a `CORS_ALLOWED_ORIGINS` del backend de producción
   (cambio de configuración del backend → gate humano).
2. **Aprobación visual** sobre staging y sustitución de los assets provisionales.
3. **Producción**: cambiar la configuración de build del proyecto de Pages de
   `artesanfc.com` de `frontend/` a `web/` (dashboard; no versionado).
4. **Rollback**: volver a apuntar el proyecto a `frontend/` (sin build) o hacer
   _rollback_ al deployment anterior en el dashboard de Pages. `frontend/` no se
   elimina hasta que la nueva versión lleve un periodo estable en producción.

## Assets pendientes (todo lo visible hoy es PROVISIONAL)

Los archivos de `public/media/placeholders/` son texturas abstractas generadas
por `scripts/generate-placeholders.mjs`; no representan ninguna pieza, persona
ni lugar, y la página los etiqueta como provisionales.

| Asset                            | Estado                                                | Necesario                                                                                                                                |
| -------------------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Video hero                       | Provisional (textura, VP8/WebM, ~300 KB)              | Clip documental ~9 s, sin audio: horizontal 1920×1080 y vertical 1080×1920, MP4 H.264 + WebM, ≤4 MB c/u, con autorización de publicación |
| Póster hero                      | Provisional                                           | Fotograma real del clip (AVIF/WebP/JPEG, horizontal y vertical)                                                                          |
| Imagen de entrada a la colección | Provisional                                           | Fotografía real autorizada, 4:5                                                                                                          |
| Fotos de piezas y artesanos      | La API devuelve `/media/...`, que **nadie sirve** hoy | Capa de media/CDN (PEND-033) + fotos autorizadas                                                                                         |
| Modelo 3D máscara de Cuilápam    | No existe                                             | GLB 2–8 MB, texturas 1024–2048 px, servido con CORS desde el origen de media                                                             |
| Canal de contacto                | No existe                                             | Correo o formulario para el bloque de cierre                                                                                             |
| Logo                             | Texto provisional                                     | Logo final (DESIGN_SYSTEM §22)                                                                                                           |

Las 158 fotos de `Fotos_Mask_1/` (fuera del repo) no se usan: su autorización de
publicación no está documentada y su compresión (WhatsApp, 960×1280) es
insuficiente para fotogrametría de calidad.

## Presupuestos medidos (`npm run measure:assets`)

JS inicial gzip: home ≈1 KB, páginas con datos ≈73–79 KB; CSS ≈7 KB;
`model-viewer` + three.js ≈280 KB gzip **solo bajo demanda**; video provisional
≈300 KB por orientación; imágenes provisionales ≤32 KB.
