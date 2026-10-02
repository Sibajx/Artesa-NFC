# ArtesaNFC — Arquitectura vigente

**Estado:** Implementada de forma incremental; los estados operativos externos
se indican con fecha
**Dominio:** `artesanfc.com`  
**Fecha de referencia:** 2026-10-02

## 1. Estado actual del repositorio

El repositorio contiene cuatro superficies implementadas: el frontend público
Astro en `web/`, el frontend anterior conservado en `frontend/`, la API FastAPI
en `backend/` y Gestión en `admin/`. PostgreSQL es la fuente de verdad. Media,
certificados/NFC y el sistema D10 de backup forman parte del código actual.

El estado de despliegue documentado difiere del estado del código:

| Superficie | Código actual | Estado operativo documentado |
|---|---|---|
| Público | `web/` (Astro) y `frontend/` (anterior) | `web/` está desplegado en el proyecto Pages de staging; su dominio propio figura pendiente. `frontend/` sigue en producción como rollback hasta el cambio manual (`web/README.md`, estado fechado 2026-09-30) |
| API | `backend/`, FastAPI + PostgreSQL | Producción detrás de Cloudflare Tunnel; controles verificados en `OPERATIONS.md` |
| Gestión | `admin/` + `/api/admin/v1` | Implementación fases 1–4; su activación depende de Access, variables y servicios externos descritos en `admin/README.md` |
| Media | `/media/`, carga desde Gestión y derivados públicos | Código implementado; regla A verificada para `GET|HEAD /media/*` el 2026-09-30. El respaldo M3 de originales está implementado, sin activación registrada en el repo |
| Backup | D10.1–D10.3 para PostgreSQL | `BACKUP.md` registra activación B2 y restore drill; es evidencia operativa fechada |

El contenido real vive en PostgreSQL y se gestiona fuera del repositorio. Este
documento no infiere qué artesanos o piezas existen ni su estado de publicación.

La base auditada antes de crear esta rama fue `origin/develop@8368c51`,
sincronizada con el checkout, y `origin/main@6e59508`. El único cambio de
contenido entre ambas era #168: `qa/db/legacy-inventory.sql`. Ese script solo
inventaría tablas legacy dentro de una transacción `READ ONLY`; no las migra ni
las elimina.

El repositorio nació con un prototipo de sitio estático, Cloudflare Worker,
Cloudflare D1 y certificados en Cloudflare (`public/`, `src/`, `db/`,
`wrangler.toml`). Ese árbol se retiró bajo F-07 y sigue en el historial de git.

## 2. Topología vigente

```text
USUARIO
  │ HTTPS
  ▼
artesanfc.com / proyecto Pages de staging
Cloudflare Pages
frontend/ (producción documentada) / web/ (Astro, staging project)
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

Gestión usa una entrada separada:

```text
gestion.artesanfc.com
Cloudflare Access + Tunnel
  ├── /api/admin/*  → FastAPI 127.0.0.1:8000
  └── resto         → admin/dist en 127.0.0.1:8003
```

Los derivados públicos de media se sirven desde
`https://api.artesanfc.com/media/*` cuando `MEDIA_ROOT` está configurado. Los
originales nunca se exponen.

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
  aplicada: 10 solicitudes por periodo de 10 segundos por IP, Block con
  mitigación de 10 segundos).
- Restricción del host de la API a `/api/v1/*` y a `GET|HEAD /media/*`, con
  bloqueo del resto (regla A aplicada), más bloqueo de `POST` a resolve con
  query string (regla B aplicada).
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

## 4. Estructura actual del repositorio

```text
artesa-nfc/
│
├── web/                 Astro estático; destino de migración y staging
├── frontend/            frontend anterior; producción/rollback documentados
├── admin/               Gestión React/Vite
├── backend/
│   ├── app/
│   │   ├── api/         API pública y API de Gestión
│   │   ├── cli/         provisioning de certificados/NFC
│   │   ├── models/      SQLAlchemy
│   │   └── services/    contenido, certificados, NFC y media
│   ├── alembic/         migraciones PostgreSQL
│   ├── ops/             release, deploy y backup D10
│   └── tests/
├── docs/                contratos, decisiones y runbooks
├── qa/                  QA y utilidades de inspección de solo lectura
├── .github/workflows/   CI de backend, web, Gestión y releases
├── README.md
└── .gitignore
```

## 5. Rutas públicas

```text
GET /
GET /artesanos
GET /artesanos/{slug}
GET /piezas
GET /piezas/{slug}
GET /c/{token}
```

La pieza pública enlaza al artesano y el perfil del artesano muestra sus piezas.
`web/` responde rutas desconocidas con `404.html`; los detalles y certificados
usan shells neutros y cargan datos desde la API. El mapa completo está en
`web/README.md`.

### Publicación en las rutas públicas (hallazgo F-08)

La API pública es la **única autoridad de publicación**. Ningún frontend
contiene páginas versionadas por entidad:

- `web/` reescribe `/piezas/{slug}` y `/artesanos/{slug}` a
  `/shell/pieza/` y `/shell/artesano/`. `frontend/`, conservado para
  producción/rollback, usa los shells equivalentes bajo `/_shell/`.
- `404` (slug desconocido, borrador, archivado, pieza bajo artesano no
  publicado: el API no los distingue y la página tampoco) → una única página
  "no disponible" con `noindex`. `5xx`, `429`, timeout, red, respuesta
  malformada, host sin base de API o URL que no sea exactamente un slug →
  "no disponible ahora", reintentable, con `noindex`. Sin JavaScript: solo un
  aviso neutro.
- `/piezas/` y `/artesanos/` no traen tarjetas: se llenan desde el API. Un `200`
  con `data: []` muestra un estado vacío, no conserva nada anterior.
- Las listas no traen entidades embebidas: se llenan desde la API. Un `200` con
  `data: []` muestra un estado vacío.
- Las reglas usan el placeholder `:slug`, no `*`, para no tapar las listas. Los
  shells viven fuera de `/piezas/` y `/artesanos/`; ver
  `docs/QA_PRIVATE_ROUTE.md` §5.
- SEO: los metadatos (`<title>`, description, canonical) los pone el JS solo con
  un `200` válido. Costo asumido y documentado: sin metadatos por entidad en el
  HTML servido. Una evolución posible es pre-renderizar solo lo publicado, con
  verificación en runtime.

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

## 7. API actual

Prefijo público:

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
/api/admin/v1
```

El namespace administrativo implementa lectura y escritura de artesanos y
piezas, transiciones de estado, disponibilidad, auditoría y media. Cloudflare
Access y `ADMIN_EMAILS` protegen toda la superficie. Certificados y NFC siguen
en la CLI de provisioning. El contrato definitivo está en
`API_CONTRACT.md` §14; no se usa el namespace histórico `/api/v1/admin`.

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

La implementación actual recibe media desde Gestión, conserva originales bajo
`MEDIA_ROOT/originales/`, genera derivados en `MEDIA_ROOT/publico/` y expone
solo estos últimos por `/media/`. Sin `MEDIA_ROOT`, la ruta responde 404 y las
cargas administrativas 503. Los formatos, límites, privacidad y respaldo se
definen en `MEDIA.md`.

## 11. Entornos

```text
local
test
staging
production
```

Hosts de producción documentados:

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

### Base de la API en los frontends públicos

Cada frontend conserva su configuración explícita por hostname:
`frontend/assets/js/api-config.js` para el rollback y
`web/src/lib/api-config.ts` para Astro. Ambas evitan comodines y loopback desde
hosts no locales.

| Hostname de la página | Base de la API |
|---|---|
| `localhost`, `127.0.0.1` | `http://127.0.0.1:8000/api/v1` |
| `artesanfc.com` | `https://api.artesanfc.com/api/v1` |
| `staging.artesanfc.com` | `https://api.artesanfc.com/api/v1` en `web/` |
| cualquier otro (`www`, `*.pages.dev`, `file://`, `[::1]`, …) | sin resolver |

Staging requiere su origen en `CORS_ALLOWED_ORIGINS`; ver `web/README.md`.

- Con la base sin resolver, el cliente no hace ninguna petición de red. Las
  páginas públicas de detalle y las listas
  muestran su estado neutro "no disponible" (F-08: ya no hay contenido estático
  de reserva) y `/c/{token}` muestra su estado de error de servicio. Un host no-local nunca puede resolver a
  loopback (guardia en `api-config.js`).
- Ninguna página HTML debe declarar una URL adicional de la API.
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

## 15. Historia de migración

Las fases siguientes explican cómo se llegó a la arquitectura actual. No son
una lista del estado presente.

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

**Estado:** completado en el repositorio. El backend implementa modelos y ciclo
de vida de certificados/NFC, resolución privada y provisioning por CLI.

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

### G — Migrar el frontend público a Astro

`web/` implementa la nueva aplicación y dispone de CI, un proyecto Pages de
staging y scripts de despliegue con rollback. En el último estado operativo
versionado (2026-09-30), ese proyecto sirve `web/`, el dominio propio de staging
figura pendiente y producción conserva `frontend/`; el cambio de producción
requiere el gate humano de `web/README.md`.

## 16. Regla de dependencias

Frontend depende de `API_CONTRACT.md`.

Backend depende de `DATA_MODEL.md`, `SECURITY.md` y `API_CONTRACT.md`.

Ningún agente debe asumir silenciosamente estructuras no documentadas de la otra capa.
