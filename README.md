# ArtesaNFC

Plataforma digital para presentar, documentar y autenticar piezas
artesanales únicas de Oaxaca, conectando cada pieza física con su
experiencia digital mediante tecnología NFC.

**Dominio:** `artesanfc.com`

**Fuente de verdad:** este README es solo una introducción rápida. La
arquitectura, el modelo de datos, el contrato de API y las decisiones de
seguridad viven en [`docs/`](docs/) — en particular
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/DATA_MODEL.md`](docs/DATA_MODEL.md),
[`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) y
[`docs/SECURITY.md`](docs/SECURITY.md). Ante cualquier discrepancia entre
este README y `docs/`, `docs/` es la referencia correcta.

## Arquitectura actual

El proyecto está en migración incremental (`docs/ARCHITECTURE.md` §15)
desde un prototipo estático hacia un backend propio:

| Capa | Tecnología | Estado |
|---|---|---|
| Frontend | Cloudflare Pages, sitio estático (`frontend/`) | Home + páginas públicas de artesano/pieza de Sprint 2, con contenido fixture/demo (`docs/SPRINT_2.md`) |
| Backend | FastAPI + PostgreSQL (`backend/`) | API pública de Sprint 3 (`docs/SPRINT_3.md`) |
| ORM / migraciones | SQLAlchemy + Alembic | Modelos `Artisan`, `Piece`, `MediaAsset` |

El frontend de Sprint 2 **todavía no consume** esta API — sigue usando
contenido fixture estático. Conectar el frontend a la API es un trabajo
de seguimiento explícitamente diferido (`docs/SPRINT_3.md` §12), no
parte de este cierre de sprint.

## API pública actual

Definida en detalle en [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md):

```text
GET /api/v1/artisans
GET /api/v1/artisans/{slug}
GET /api/v1/pieces
GET /api/v1/pieces/{slug}
```

Certificados, NFC y ownership (`POST /api/v1/certificates/resolve` y
todo lo relacionado) son alcance de **Sprint 4** (`docs/WORKFLOW.md`
§14) y no existen todavía en este repositorio.

## Estructura del repositorio

```text
artesa-nfc/
├── frontend/       Sitio estático (Sprint 2), sin integración con la API todavía
├── backend/        FastAPI + PostgreSQL (Sprint 3) — ver backend/README.md
├── docs/           Fuente de verdad: producto, arquitectura, datos, API, seguridad
├── public/, src/, db/, wrangler.toml
│                   Legado del prototipo original en Cloudflare Pages/D1/Workers,
│                   conservado únicamente como estado de migración
│                   (docs/ARCHITECTURE.md §1, §15); no es la implementación activa
└── README.md
```

## Empezar a trabajar en el backend

Ver [`backend/README.md`](backend/README.md) para el flujo local de
desarrollo (base de datos, migraciones, seed, servidor, pruebas).

## QA de la ruta privada de certificados

`./qa/validate-private-route.sh` levanta una base PostgreSQL desechable, la API
y el frontend, y verifica con un navegador real `/c/{token}` (rutas, tokens
válidos/inválidos/revocados, aislamiento público y privacidad del token). Ver
[`docs/QA_PRIVATE_ROUTE.md`](docs/QA_PRIVATE_ROUTE.md).

## Estado del proyecto

Ver el cierre de cada sprint en `docs/`:
[`SPRINT_0.md`](docs/SPRINT_0.md),
[`SPRINT_1.md`](docs/SPRINT_1.md),
[`SPRINT_2.md`](docs/SPRINT_2.md),
[`SPRINT_3.md`](docs/SPRINT_3.md).
