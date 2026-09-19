# Sprint 4 — NFC y certificados privados — Cierre / QA final

**Estado:** QA COMPLETADO — **READY FOR RELEASE** (el bloqueador B1 hallado
durante el QA fue corregido y reverificado; ver sección 12)
**Fecha de referencia:** 2026-09-19
**Alcance:** flujo de certificado privado y NFC de extremo a extremo:
modelos, ciclo de vida, `POST /api/v1/certificates/resolve`, ruta privada
`/c/{token}` y endurecimiento de seguridad, sobre la base de Sprint 3
(`docs/SPRINT_3.md`).

**Issue de seguimiento:** #61 — Sprint 4 — NFC and private certificates.
Este documento no cierra #61 por sí mismo: eso corresponde al merge/cierre
en GitHub.

Este QA fue de verificación: no se agregó funcionalidad. La primera pasada
encontró un bloqueador (B1, sección 12); tras aprobación de Alexis se
aplicó **una única corrección de una línea en `frontend/_redirects`** y se
repitió la verificación relevante. Cambios de código/configuración de todo
el QA: solo esa línea. Sin cambios de backend, CSS ni JS.

## 1. Objetivo

Que un visitante que escanea el NFC de una pieza llegue a `/c/{token}` y
el backend valide el certificado con un token no enumerable, sin exponer
nunca token, `token_hash`, UID físico ni datos internos, y con un único
resultado público genérico (`unavailable`) para todo caso no válido
(`docs/API_CONTRACT.md` §7, `docs/SECURITY.md` §4).

## 2. Trabajo completado

| Issue* | Alcance | PR |
|---|---|---|
| #62 | Modelo `Certificate` + migración `8c2f9a74e447` | #63 |
| #64 | Ciclo de vida del token (activar / revocar / rotar), SHA-256 | #65 |
| #66 | `POST /api/v1/certificates/resolve` | #67 |
| #68 | Modelo `NfcTag` + migración `9b8bb430770d` | #69 |
| #70 | Ciclo de vida NFC + migración `895974720462` (`locked_at` histórico) | #71 |
| #72 | Ruta privada `/c/{token}` (`frontend/c/`, `_headers`, `_redirects`) | #73 |
| #74 | Endurecimiento del flujo de resolve (no-store, `hide_parameters`, Nginx) | #75 |

Trabajo previo relacionado, ya en `develop`: CORS con allowlist (#57) e
integración frontend↔API pública (#58).

\* Los números de Issue se **infieren** de la numeración de PR (Issue = PR − 1)
y de comentarios en código (`Issue #70`, `Issue #72`, confirmados); no se
pudieron verificar contra GitHub (`gh` sin autenticar en este QA).

## 3. Arquitectura

```text
NFC tag ─► https://artesanfc.com/c/{token}   (Cloudflare Pages, shell estático)
              │  hydrate-certificate.js lee el token SOLO de location.pathname
              ▼
   POST {api}/api/v1/certificates/resolve  {"token": "..."}   (Nginx: 30 r/m/IP, body ≤ 1k)
              ▼
   FastAPI ─ forma plausible? ─► SHA-256 ─► lookup por token_hash
              │  JOIN piece+artisan con el invariante de publicación
              ▼
   authentic {authenticity, piece, artisan, authenticity_metadata{notes}}
   unavailable {authenticity{status}}      ← único cuerpo para todo lo demás
```

- Token: 256 bits CSPRNG, Base64 URL-safe, 43 caracteres; solo se guarda
  `SHA-256(token)` (`certificate.token_hash`, `UNIQUE`).
- `authenticity_metadata` se expone por allowlist (solo `notes`).
- El invariante pieza+artesano publicados reutiliza el mismo predicado que
  la API pública (`piece_artisan_published_conditions`).
- `physical_uid` es metadato de inventario: ningún servicio, esquema ni
  endpoint lo lee para autenticar (verificado por búsqueda en `app/`).
- Rutas registradas: las 4 `GET` públicas, `POST …/resolve` y `/health`.
  No existe endpoint de certificado/NFC/admin adicional.

## 4. Ciclo de vida del certificado

`draft → active → revoked` (`certificate_status`). Reglas, en BD y servicio:

- `ck_certificate_token_hash_matches_status`: `token_hash` NULL solo en
  `draft`. `ck_certificate_revoked_at_matches_status`.
- `uq_certificate_one_active_per_piece` (índice único parcial): un solo
  `active` por pieza; el historial `revoked` se conserva.
- `rotate_certificate`: SAVEPOINT + `SELECT … FOR UPDATE`; revoca el
  activo y emite uno nuevo con token nuevo; rollback completo si falla.
- Secreto: `raw_token` solo existe en el resultado de activación/rotación
  (`repr=False`); nunca se persiste, se registra ni aparece en excepciones.

Cubierto por `test_certificate_lifecycle.py` (33 pruebas). Verificado
además en vivo: rotación → token viejo `unavailable`, nuevo `authentic`.

## 5. Ciclo de vida NFC

`available → programmed → locked`, con `replaced` (superado por otro tag)
y `retired` (fuera de servicio) como estados terminales distintos.

- `assign` solo fija `piece_id`; `program` exige asignación y ningún otro
  tag activo; `lock` solo desde `programmed`; `retire` desde
  `available/programmed/locked`; sin idempotencia inventada.
- `replace_nfc_tag`: SAVEPOINT + `FOR UPDATE` sobre el tag activo y el de
  reemplazo; rollback completo si falla.
- `uq_nfc_tag_one_active_per_piece` (parcial, `programmed|locked`);
  `ck_nfc_tag_assignment_requires_piece`; `locked_at` se conserva como
  historial en `replaced/retired` (migración `895974720462`).
- Historial preservado: nunca se borra una fila; `physical_uid`,
  `programmed_at`, `locked_at` intactos al reemplazar/retirar.

Cubierto por `test_nfc_tag_lifecycle.py` (35 pruebas).

## 6. Ruta privada `/c/{token}`

- `frontend/c/index.html` (shell) + `hydrate-certificate.js` + `api.js`
  (`resolveCertificate`, que distingue fallo de transporte de `unavailable`).
- Cuatro estados excluyentes: cargando, auténtico, `unavailable`, error
  temporal; más aviso sin JS. Fallo de red/timeout(5 s)/HTTP≠2xx/cuerpo
  malformado → **error temporal**, nunca `unavailable`.
- Forma de ruta canónica: exactamente un segmento `[A-Za-z0-9_-]{1,256}`;
  `/c/`, `/c/foo/bar`, `%` inválido, caracteres extra o >256 → `unavailable`
  **sin llamada a la API**. Query/fragmento se ignoran.
- Enlaces a pieza/artesano solo con slugs públicos.
- `_headers` (`/c/*`): `Cache-Control: no-store`, `X-Robots-Tag: noindex,
  nofollow`, `Referrer-Policy: no-referrer`; meta `robots` y `referrer`
  equivalentes en el HTML.

## 7. Endurecimiento de seguridad

- `ResolveNoStoreMiddleware`: `Cache-Control: no-store` en toda respuesta
  de resolve (200/422/405/redirect), solo en esa ruta.
- `create_engine(hide_parameters=True)`: sin parámetros ligados en errores.
- Errores 422 sin eco del cuerpo/token; 500 con envoltura genérica.
- CORS: allowlist explícita, sin credenciales, `GET`/`POST`.
- Nginx (ejemplo): 30 r/m/IP resolve (burst 5), 120 r/m/IP GET público
  (burst 20), `client_max_body_size 1k`, `X-Forwarded-For` sobrescrito con
  `$remote_addr`, `no-store` en 429/413 del location de resolve, log de
  acceso sin body ni query, `/health`/`/docs` no expuestos.

## 8. Resultados de pruebas

`pytest -q` contra PostgreSQL 16 real (contenedor efímero): **240 passed**,
0 fallos (dos corridas consecutivas, 1.9 s).

| Archivo | Pruebas |
|---|---|
| test_api_artisans | 14 |
| test_api_certificates_resolve | 29 |
| test_api_pieces | 22 |
| test_certificate_lifecycle | 33 |
| test_certificate_resolve_hardening | 28 |
| test_config | 5 |
| test_cors | 12 |
| test_error_envelopes | 3 |
| test_health | 2 |
| test_models_schema | 45 |
| test_nfc_tag_lifecycle | 35 |
| test_seed | 12 |

(Sprint 3 cerró con 60; Sprint 4 agrega 180.) Nota: `test_seed.py` hace
commit real de los datos demo en la BD objetivo (diseño de Sprint 3); las
demás pruebas usan transacción con rollback.

## 9. QA final (2026-09-19)

Todo contra PostgreSQL 16 y FastAPI reales; navegador Chromium
(Playwright), escritorio 1280×800 y móvil 390×844.

| Área | Resultado |
|---|---|
| Migraciones (BD limpia) | `upgrade head` → `895974720462`; 6 tablas, 7 enums; `downgrade` paso a paso hasta base sin enums huérfanos; re-`upgrade` idéntico; `alembic check`: sin drift. |
| Downgrade con datos | Con un tag `retired` que conserva `locked_at`, `downgrade -1` falla de forma atómica (`CheckViolation`), revisión y datos intactos. Limitación conocida; no se mutaron datos históricos. |
| Resolve real (HTTP) | 47/47 verificaciones: auténtico; token alterado / desconocido / longitud / alfabeto / vacío / 200 chars; revocado; pieza no publicada; artesano no publicado; rotación. **Todas las causas `unavailable` son idénticas byte a byte** (status, cuerpo, content-type, longitud, cache-control). |
| Log del API | 0 apariciones de token, `token_hash`, prefijos, UID o contraseña. |
| API pública | 4 endpoints `GET` correctos, sin campos de certificado/NFC/privados; 404 canónico; no existen endpoints de certificado/NFC/admin. |
| Nginx | `nginx -t` OK (1.30.5) sobre el archivo sin modificar. En una copia solo con `listen` cambiado: 6 POST pasan y el 7.º → 429 (JSON, `Retry-After: 2`, `no-store`) incluso con `X-Forwarded-For` aleatorio; GET público: 21 pasan, luego 429; body >1k → 413 (HTML por defecto); `X-Forwarded-For` recibido por el upstream = IP del peer. |
| Navegador (`/c/…`) | Primera pasada: `/c/{token}` servía Home (B1). **Tras la corrección**, contra el `frontend/` real del repo: **97/97** verificaciones en escritorio y móvil (dos corridas), más 24/24 rutas públicas (12 × 2 viewports). Ver secciones 10 y 12. |
| Frontend público | `/`, `/artesanos/`, `/artesanos/{slug}`, `/piezas/`, `/piezas/{slug}`: 200, sin overflow, sin errores, contenido hidratado desde la API, ningún enlace a `/c/`. Con API caída, el fallback estático sigue mostrando contenido. |

### Privacidad del token y cabeceras (navegador real)

Verificado en ambos viewports: token ausente del texto renderizado, del
`outerHTML`, de todo atributo, `href`/`src`, `window.name`, `history.state`;
`localStorage`, `sessionStorage`, IndexedDB, Cache Storage, service workers
y cookies vacíos; el token viaja solo en el cuerpo del único POST a resolve;
ninguna otra URL/cabecera lo contiene; sin `token_hash`/`physical_uid` en
DOM ni respuesta; **cero salida de consola desde JS propio**; al navegar a
pieza/artesano no hay `Referer` y `document.referrer` es vacío. El token
permanece en `location.pathname` (esperado).

(Estas verificaciones se hicieron primero contra una copia con la corrección
y se **repitieron íntegras contra el repo corregido**, sección 12.)

Cabeceras del documento `/c/*` (servidas por Wrangler 4.135, que aplica la
semántica de Cloudflare Pages): `Cache-Control: no-store`,
`X-Robots-Tag: noindex, nofollow`. `Referrer-Policy` llega como
`strict-origin-when-cross-origin, no-referrer` porque Pages **concatena** la
regla global `/*` con la de `/c/*`; por la especificación gana el último
token reconocido y se verificó en Chromium sin la meta (sin `Referer` de
primera parte). Hallazgo menor: solo las peticiones de fuentes iniciadas por
la CSS de Google Fonts llevan `Referer: https://fonts.googleapis.com/`
(no contiene página ni token).

## 10. Estados de la ruta privada verificados

Token auténtico; desconocido; alterado; revocado; `/c/`; `/c/foo/bar`;
`/c/abc.def`; `%E0%A4%A`; token de 300 caracteres; token en query/fragmento
(ignorado); backend caído **real** (proceso detenido) y simulado (abort,
HTTP 500, 429 estilo Nginx, cuerpo 200 malformado, colgado→timeout 5 s) →
estado de error temporal, sin `unavailable`; JavaScript deshabilitado.

## 11. Limitaciones conocidas (reconfirmadas)

| Limitación | Estado |
|---|---|
| Rate limit de Nginx es por nodo | Vigente |
| IP real vía Cloudflare pendiente de topología | Vigente (`real_ip_*` comentado; XFF de cliente no se confía) |
| Cooldown por abuso sostenido | No implementado (sin código) |
| Enforcement en producción | No verificado: solo ejemplo de config probado en local |
| 500 no controlado en resolve sin `no-store` | **Confirmado**: el 500 de la app no lo lleva (el handler global queda fuera del middleware); el location de Nginx de ejemplo sí lo añade |
| 413 de Nginx devuelve HTML por defecto (con versión de nginx: falta `server_tokens off`) | Vigente |
| IPv6: límite por dirección (evasión con /64) | Vigente |
| `AUDIT_EVENT` diferido | Vigente (sin modelo/servicio/API) |
| Sin prueba de concurrencia multi-conexión real | Vigente: los `FOR UPDATE` y los índices únicos parciales están probados por lógica/constraint, no con dos conexiones concurrentes |
| Downgrade `895974720462` depende de datos | Vigente (sección 9) |
| Media de demo (`/media/...`) no se sirve | Vigente (Sprint 3); el frontend cae al placeholder |
| Todas las páginas fijan `artesanfc-api-base` a `http://127.0.0.1:8000` | Requisito previo de despliegue |
| Sin CSP ni HSTS en `_headers` (`SECURITY.md` §11) | Diferido a despliegue |
| `/nosotros` y `/contacto` no son rutas propias | Sirven Home (fallback SPA de Pages); en Home son anclas `#about`/`#contact` |
| `backend/README.md` "Sprint boundary" desactualizado | Aún dice que certificados/NFC no están implementados |

## 12. Bloqueador B1 — hallado durante el QA, corregido y reverificado

### Hallazgo original

`frontend/_redirects:7` contenía:

```text
/c/*  /c/index.html  200
```

El parser de redirecciones de Cloudflare Pages rechaza toda regla `…/*` cuyo
destino termine en `/index` o `/index.html` (`wrangler pages dev` 4.135.0:
*"Infinite loop detected in this rule and has been ignored"*, `Parsed 0 valid
redirect rules`). Con la regla ignorada, `/c/{token}` caía en el fallback SPA
y devolvía **la página Home** (`<title>ArtesaNFC</title>`), no el shell del
certificado; solo `/c/` (sin token) llegaba a `frontend/c/index.html`. Efecto:
la URL escrita en un tag NFC nunca habría mostrado el certificado. Las pruebas
jsdom de #72 no podían detectarlo; lo reveló el QA con navegador real.

### Corrección (aprobada por Alexis, 2026-09-19)

Única línea modificada de todo el repo en este QA:

```diff
-/c/*  /c/index.html  200
+/c/*  /c/  200
```

Sin mover archivos y sin tocar ninguna otra regla, backend, CSS ni JS.

### Reverificación contra el repo corregido (`frontend/` real)

- **Wrangler Pages dev 4.135.0:** `Parsed 1 valid redirect rule`, sin
  advertencia de regla inválida (la única advertencia es la ajena
  `compatibility_date`).
- **Rutas:** `/c/{token}`, `/c/`, `/c/foo/bar` y `/c/{token}?x=1` → 200 con
  el shell (`<title>ArtesaNFC — Certificado</title>`); `/c` → 308 a `/c/`.
  La URL del navegador permanece `/c/{token}`. Rutas públicas sin cambios
  (`/`, `/artesanos/…`, `/piezas/…`, `/nosotros`, `/contacto`).
- **Cabeceras de `/c/*`:** `Cache-Control: no-store`, `X-Robots-Tag: noindex,
  nofollow`, `Referrer-Policy: strict-origin-when-cross-origin, no-referrer`
  (efectivo `no-referrer`, confirmado en Chromium sin la meta: ningún `Referer`
  de primera parte); meta `robots` y `referrer` presentes.
- **Chromium, escritorio y móvil, 97/97 (dos corridas idénticas):** shell
  renderizado y no Home (título, contenedores de estado, sin `#hero`); URL
  intacta; auténtico; desconocido / alterado / revocado; `/c/`; `/c/foo/bar`
  (llega al shell y se rechaza en cliente sin llamar a la API); caracteres
  inválidos, `%` inválido, 300 caracteres; query/fragmento ignorados; backend
  caído **real** y simulado (abort, 500, 429, cuerpo malformado, timeout 5 s)
  → error temporal, nunca `unavailable`; sin JS.
- **Sin regresión de layout:** las 5 hojas de estilo cargan `200 text/css`
  desde rutas absolutas; sin overflow horizontal; tipografía aplicada; captura
  móvil idéntica a la verificada antes de la corrección.
- **Privacidad del token:** ausente de texto, HTML y atributos generados por
  nuestro código; `localStorage`, `sessionStorage`, IndexedDB, Cache Storage,
  service workers y cookies vacíos; cero salida de consola de JS propio; el
  token solo viaja en el cuerpo del único POST a resolve; enlaces a
  pieza/artesano (`/piezas/mascara-demo-01/`, `/artesanos/artesano-demo-01/`)
  sin token, sin `Referer` y con `document.referrer` vacío.
- **Regresión pública:** 12 rutas × 2 viewports (24/24): 200, contenido
  hidratado (API 200), hojas de estilo correctas, sin overflow ni errores, sin
  enlaces a `/c/`.
- **Backend:** no se tocó ningún archivo de backend; no se repitió la suite de
  240 pruebas (resultado de la sección 8 sigue vigente).

Alcance de la verificación: emulador local de Cloudflare Pages (Wrangler), no un
despliegue real. Ver lista de despliegue en la sección 13.

## 13. Trabajo diferido y verificación posterior al despliegue

Diferido: `AUDIT_EVENT`; cooldown; test de concurrencia con múltiples
conexiones; CSP/HSTS; `server_tokens off` y página 413 JSON; capa de media
real; `artesanfc-api-base` por entorno; API administrativa; ownership; páginas
`/nosotros` y `/contacto` propias.

Verificación obligatoria en el primer despliegue real (no afirmada aquí):

1. En la vista previa/producción de Cloudflare Pages: `/c/{token}` sirve el
   shell (no Home) con las tres cabeceras privadas.
2. Enforcement real de los límites y del 413 de Nginx; IP real del cliente
   según la topología de Cloudflare.
3. `artesanfc-api-base` y `CORS_ALLOWED_ORIGINS` apuntando a los orígenes
   reales (hoy todas las páginas usan `http://127.0.0.1:8000`).

## 14. Preparación para release

**READY FOR RELEASE**

Justificación: 240/240 pruebas de backend sobre PostgreSQL real; cadena de
migraciones verificada; resolve real con convergencia byte a byte de todos los
casos `unavailable`; privacidad del token, cabeceras privadas y endurecimiento
verificados en navegador real; el bloqueador B1 (sección 12) fue corregido con
un cambio de una línea y reverificado contra el repo corregido. Las
limitaciones de la sección 11 y los puntos de la sección 13 son conocidos y no
bloqueantes para este release; no se afirma enforcement en producción.

## 15. Nota posterior al Sprint 4 — base de la API (hallazgo F-01)

Las secciones 11 y 13 se conservan como el hallazgo original. Corrección
posterior (rama `fix/production-api-base`): la base de la API ya no se declara
por página. `frontend/assets/js/api-config.js` la elige por hostname exacto
(`localhost`/`127.0.0.1` → API local; `artesanfc.com` →
`https://api.artesanfc.com/api/v1`; cualquier otro host → sin resolver, sin
peticiones de red) y se eliminaron los 10 `<meta name="artesanfc-api-base">`.
Regla vigente en `ARCHITECTURE.md` §11. Sigue pendiente, como tarea de
despliegue, que el backend de producción permita `https://artesanfc.com` en
`CORS_ALLOWED_ORIGINS`.
