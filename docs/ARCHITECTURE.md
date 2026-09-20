# ArtesaNFC — Arquitectura objetivo

**Estado:** Propuesta aprobada para implementación incremental  
**Dominio:** `artesanfc.com`  
**Fecha de referencia:** 2026-09-16

## 1. Estado actual del repositorio

El repositorio nació con un prototipo basado en sitio estático, Cloudflare Worker, Cloudflare D1 y lógica de certificados en Cloudflare (`public/`, `src/`, `db/`, `wrangler.toml`). Ese árbol legado **fue eliminado del repositorio** bajo el hallazgo F-07 (ver §15 F); sigue recuperable en el historial de git. La estructura actual es `frontend/`, `backend/`, `qa/`, `docs/` y `.github/`.

La migración fue incremental: el sistema anterior no se eliminó hasta que existió reemplazo funcional.

## 2. Arquitectura objetivo

```text
USUARIO
  │ HTTPS
  ▼
artesanfc.com
Cloudflare Pages
Frontend público
  │
  │ HTTPS / JSON
  ▼
api.artesanfc.com
Cloudflare (edge: reglas y rate limiting)
  │
  ▼
Cloudflare Tunnel (cloudflared)
  │  http://localhost:8000
  ▼
Uvicorn / FastAPI
(127.0.0.1:8000, artesa-nfc.service)
  │
  ▼
PostgreSQL
Fuente de verdad
```

**Topología real de producción:** Cloudflare → Cloudflare Tunnel → Uvicorn /
FastAPI → PostgreSQL. **Nginx NO está desplegado actualmente** y no está en el
request path: `backend/nginx/artesanfc-api.conf.example` es una configuración
alternativa / de referencia, no el enforcement vigente. Qué controla cada capa,
qué está aplicado y qué está pendiente: `docs/OPERATIONS.md`.

## 3. Responsabilidades

### Frontend / Cloudflare Pages

- Home.
- Perfiles de artesanos.
- Páginas públicas de piezas.
- Navegación.
- Animaciones.
- Visualización 3D/360.
- Consumo de API.
- Presentación del certificado tras validación.

No es fuente de verdad para artesanos, piezas o certificados.

### Cloudflare (edge y Tunnel)

- Terminación TLS y HTTPS obligatorio.
- Rate limiting de `POST /api/v1/certificates/resolve` (capa primaria; regla C
  aplicada: 10 solicitudes por 10 segundos por IP).
- Restricción del host de la API al namespace público (`/api/v1/*`) con bloqueo
  del resto (regla A aplicada) y bloqueo de `POST` a resolve con query string
  (regla B aplicada).
- Entrega al origen mediante el Tunnel: el servidor no expone puertos públicos.

Estas reglas son configuración operativa **no versionada**; el repo solo las
documenta con exactitud en `docs/OPERATIONS.md`.

### Nginx (alternativa, NO desplegada)

Nginx no está en el request path de producción. El ejemplo del repo
(`backend/nginx/artesanfc-api.conf.example`) se conserva como referencia
alternativa y no debe asumirse como protección activa. Adoptarlo detrás del
Tunnel exigiría una variante distinta (HTTP en loopback, IP real desde
`CF-Connecting-IP`); ver `docs/OPERATIONS.md`.

### FastAPI

- API pública.
- API administrativa.
- Validación.
- Lógica de negocio.
- Registro de artesanos y piezas.
- Asociación pieza ↔ artesano.
- Emisión, validación y revocación de certificados.
- Generación segura de tokens.
- Acceso a base de datos.
- Logs relevantes.
- Límite de cuerpo de `POST /api/v1/certificates/resolve` (1024 bytes, `413`).
- `/health` dependiente de la base de datos (`200` / `503`, `no-store`).
- Sin `/docs`, `/redoc` ni `/openapi.json` en `staging` y `production`.

### PostgreSQL

Fuente única de verdad para artesanos, piezas, certificados, asociaciones NFC, estados y futuras extensiones de propiedad.

## 4. Estructura objetivo del repositorio

```text
artesa-nfc/
│
├── frontend/
│   ├── index.html
│   ├── artesanos/
│   ├── piezas/
│   ├── assets/
│   │   ├── css/
│   │   ├── js/
│   │   ├── img/
│   │   ├── video/
│   │   ├── models/
│   │   └── icons/
│   └── _headers
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── main.py
│   ├── migrations/
│   ├── scripts/
│   ├── tests/
│   ├── logs/
│   ├── reportes/
│   ├── nginx/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   └── .env.example
│
├── docs/
│   ├── PROJECT.md
│   ├── ARCHITECTURE.md
│   ├── DESIGN_SYSTEM.md
│   ├── API_CONTRACT.md
│   ├── DATA_MODEL.md
│   ├── SECURITY.md
│   ├── WORKFLOW.md
│   └── DECISIONS.md
│
├── .github/
│   ├── ISSUE_TEMPLATE/
│   └── workflows/
│
├── README.md
└── .gitignore
```

## 5. Rutas públicas

```text
GET /
GET /nosotros
GET /contacto
GET /artesanos
GET /artesanos/{slug}
GET /piezas
GET /piezas/{slug}
```

La pieza pública enlaza al artesano y el perfil del artesano muestra sus piezas.

### Publicación en las rutas públicas (hallazgo F-08)

La API pública es la **única autoridad de publicación**. El frontend no
contiene ninguna página por entidad:

- `/piezas/{slug}` y `/artesanos/{slug}` se sirven con **un shell neutro por
  tipo** (`frontend/_shell/pieza/`, `frontend/_shell/artesano/`) mediante las
  reglas de `frontend/_redirects`
  (`/piezas/:slug  /_shell/pieza/  200`, con y sin `/` final, y las dos
  equivalentes para `/artesanos`). El shell no contiene nombre, texto, imagen ni
  slug; `hydrate-detail.js` lee el slug de `location.pathname` y muestra la
  entidad **solo** tras un `200` válido.
- `404` (slug desconocido, borrador, archivado, pieza bajo artesano no
  publicado: el API no los distingue y la página tampoco) → una única página
  "no disponible" con `noindex`. `5xx`, `429`, timeout, red, respuesta
  malformada, host sin base de API o URL que no sea exactamente un slug →
  "no disponible ahora", reintentable, con `noindex`. Sin JavaScript: solo un
  aviso neutro.
- `/piezas/` y `/artesanos/` no traen tarjetas: se llenan desde el API. Un `200`
  con `data: []` muestra un estado vacío, no conserva nada anterior.
- Las listas y las rutas anidadas (`/piezas/a/b/`) no tienen rewrite; estas
  últimas conservan su comportamiento anterior (fallback SPA a Home).
- Reglas de `_redirects`: se usa el placeholder `:slug` y **no** `*`. Con
  Wrangler 4.135.0 la primera regla que coincide gana y un rewrite `200` se
  aplica incluso sobre un archivo existente; `/piezas/*` también coincide con
  `/piezas/` y taparía la lista (`docs/QA_PRIVATE_ROUTE.md` §5).
- Los shells viven en `/_shell/`, fuera de `/piezas/` y `/artesanos/`, para que
  ningún slug pueda chocar con ellos.
- SEO: los metadatos (`<title>`, description, canonical) los pone el JS solo con
  un `200` válido. Costo asumido y documentado: sin metadatos por entidad en el
  HTML servido. Una evolución posible es pre-renderizar solo lo publicado en un
  build, con verificación en runtime (fuera de F-08: hoy no hay build).

## 6. Certificado privado

Flujo conceptual:

```text
Tag NFC
  │
  ▼
artesanfc.com/c/{token}
  │
  ▼
Frontend solicita validación
  │
  ▼
FastAPI valida token + estado + límites
  │
  ├── válido ──► datos del certificado
  └── inválido ─► respuesta genérica
```

El token:

- no es el ID visible de la pieza;
- no es secuencial;
- no es el UID del tag;
- se genera con un CSPRNG;
- puede revocarse o rotarse según políticas futuras.

## 7. API conceptual

Prefijo recomendado:

```text
/api/v1
```

### Pública

```text
GET /api/v1/artisans
GET /api/v1/artisans/{slug}
GET /api/v1/pieces
GET /api/v1/pieces/{slug}
POST /api/v1/certificates/resolve
```

### Administrativa

```text
POST   /api/v1/admin/artisans
PATCH  /api/v1/admin/artisans/{id}
POST   /api/v1/admin/pieces
PATCH  /api/v1/admin/pieces/{id}
POST   /api/v1/admin/certificates
POST   /api/v1/admin/certificates/{id}/revoke
```

Las rutas definitivas se congelarán en `API_CONTRACT.md`.

## 8. Modelo conceptual

```text
ARTISAN
  1
  │
  N
PIECE
  1
  │
  0..1
CERTIFICATE

PIECE
  1
  │
  0..1
NFC_TAG
```

### Artisan

- UUID interno.
- slug.
- nombre.
- nombre artístico.
- localidad.
- biografía.
- historia.
- técnicas.
- fotografía.
- redes/contacto público.
- estado de publicación.

### Piece

- UUID interno.
- slug.
- `artisan_id`.
- nombre.
- código público.
- descripción.
- historia.
- técnica.
- materiales.
- fecha/año.
- dimensiones.
- origen.
- estado.
- paleta/tema visual.
- recursos multimedia.
- 3D/360 opcional.

### Certificate

- UUID interno.
- `piece_id`.
- `token_hash`.
- fecha de emisión.
- estado.
- fecha/motivo de revocación opcionales.
- versión.
- metadatos de autenticidad.

No se recomienda almacenar el token privado en texto plano si el flujo puede resolverse mediante hash.

### NFC Tag

- UUID interno.
- `piece_id`.
- tipo/modelo.
- UID físico opcional para inventario.
- fecha de programación.
- fecha de bloqueo.
- estado.
- observaciones.

El UID físico no actúa como secreto.

## 9. Datos del frontend

No se utilizará `data.json` como segunda base de datos de producción.

Permitido:
- mocks temporales;
- fixtures;
- contenido visual estático.

No permitido como fuente final de verdad:
- artesanos;
- piezas;
- certificados;
- estados;
- autenticidad.

## 10. Multimedia y 3D

Formato recomendado para modelos web:

```text
.glb
```

Cada pieza puede utilizar:
- 3D real;
- 360 fotográfico;
- galería;
- macrofotografía;
- video.

No todas las piezas necesitan 3D.

## 11. Entornos

```text
local
test
staging
production
```

Producción prevista:

```text
artesanfc.com
api.artesanfc.com
```

### Entorno del backend (`APP_ENV`)

`APP_ENV` es **obligatorio y sin valor por defecto** en el backend: si falta,
la API, Alembic y el seed se niegan a arrancar. Valores aceptados (sin
alias; `dev`, `development` y `prod` se rechazan): `local`, `test`, `staging`,
`production`.

- `DATABASE_URL` también es **obligatoria y sin valor por defecto** en todos
  los entornos: si falta, está vacía o solo tiene espacios, la API, Alembic,
  el seed y `pytest` se niegan a arrancar (`DATABASE_URL is not set`) sin
  imprimir ningún valor. `docker-compose.yml` tampoco define un respaldo: el
  contenedor `api` la toma solo de `.env`.
- `production` y `staging` rechazan la `DATABASE_URL` de desarrollo o con
  contraseña vacía/placeholder; `production` además exige
  `https://artesanfc.com` en `CORS_ALLOWED_ORIGINS` y `DEBUG=false`.
- Las pruebas (`pytest`) solo arrancan con `APP_ENV=test` **y** una base de
  datos cuyo nombre tenga el token `test` (`artesanfc_test`).
- El seed solo corre con `APP_ENV=local` (host local) o `test` (base de
  datos de prueba); nunca en `staging` ni `production`.
- `.env` se lee siempre de `backend/.env` (no depende del directorio de
  trabajo) y es opcional; las variables de entorno tienen prioridad.

Implementación y detalles: `backend/app/core/db_safety.py` y
`backend/README.md` ("Environment safety").

### Base de la API en el frontend

La base de la API tiene una sola fuente de verdad:
`frontend/assets/js/api-config.js`. La elige por **hostname exacto** de la
página (sin comodines ni configuración por HTML):

| Hostname de la página | Base de la API |
|---|---|
| `localhost`, `127.0.0.1` | `http://127.0.0.1:8000/api/v1` |
| `artesanfc.com` | `https://api.artesanfc.com/api/v1` |
| cualquier otro (`www`, `*.pages.dev`, `file://`, `[::1]`, …) | sin resolver (`null`) |

- Con la base sin resolver, `api.js` no hace ninguna petición de red
  (resultado `unavailable`): las páginas públicas de detalle y las listas
  muestran su estado neutro "no disponible" (F-08: ya no hay contenido estático
  de reserva) y `/c/{token}` muestra su estado de error de servicio. Un host no-local nunca puede resolver a
  loopback (guardia en `api-config.js`).
- Ninguna página HTML ni otro script debe declarar o duplicar la URL de la
  API (ya no existe el `<meta name="artesanfc-api-base">`).
- Soportar un host nuevo (`www`, un preview o un staging) implica añadirlo
  explícitamente a `api-config.js` **y** a `CORS_ALLOWED_ORIGINS` del backend.
- El backend de producción debe permitir el origen `https://artesanfc.com` en
  `CORS_ALLOWED_ORIGINS` (`SECURITY.md` §10). Es configuración de despliegue,
  fuera del código del frontend.

## 12. Secretos

Nunca versionar:

- `.env`;
- contraseñas;
- claves privadas;
- tokens administrativos;
- secretos de aplicación.

Versionar solo `.env.example` sin valores reales.

## 13. Seguridad mínima del certificado

1. token de alta entropía;
2. hash del token en DB cuando sea viable;
3. rate limiting;
4. `noindex`;
5. exclusión de sitemap;
6. ausencia de enlaces públicos;
7. respuestas que no faciliten enumeración;
8. logs;
9. revocación;
10. HTTPS;
11. validación estricta en backend.

## 14. NFC

Tags actuales:

```text
NTAG213
13.56 MHz
ISO 14443A
```

Reglas:

- almacenar URL HTTPS;
- validar longitud del enlace;
- probar lectura en teléfonos;
- probar antes de bloquear;
- bloquear escritura solo cuando la URL definitiva esté confirmada.

## 15. Estrategia de migración

### A — Documentar
Congelar producto, arquitectura y decisiones.

### B — Crear nueva estructura
Agregar `frontend/` y `backend/` sin borrar el legado inmediatamente.

### C — Reimplementar frontend
Nueva Home y rutas públicas.

### D — Implementar API
FastAPI + PostgreSQL.

### E — Migrar certificados
Mover el flujo privado al backend nuevo.

### F — Retirar legado
Solo después de pruebas, migración de datos y plan de rollback.

**Estado (hallazgo F-07): completado para el repositorio.** Se eliminaron
`public/`, `src/`, `db/` y `wrangler.toml`. La base D1 solo contenía datos de
ejemplo (sin certificados ni registros de producción) y ninguna etiqueta NFC
física apuntaba a rutas `/cert/<ID>`; el rollback es un `git revert`.

**Pendiente, seguimiento operativo separado:** la limpieza de los recursos del
dashboard de Cloudflare (aplicación Worker `artesa-nfc` conectada a Git, base D1
`artesanfc-db`, asociación del dominio) no forma parte del repositorio y no se
considera completada aquí.

## 16. Regla de dependencias

Frontend depende de `API_CONTRACT.md`.

Backend depende de `DATA_MODEL.md`, `SECURITY.md` y `API_CONTRACT.md`.

Ningún agente debe asumir silenciosamente estructuras no documentadas de la otra capa.
