# ArtesaNFC

Plataforma para documentar y autenticar piezas artesanales de Oaxaca mediante
un catálogo público, Gestión y certificados vinculados a etiquetas NFC.

## Estado actual

Este resumen refleja el código en `develop` al 2026-10-02. El estado operativo
externo se toma de los runbooks fechados; el repositorio no permite comprobar
por sí solo el contenido ni la configuración viva de producción.

| Área | Implementación en el repositorio | Estado documentado |
|---|---|---|
| Sitio público | Astro estático en [`web/`](web/) y frontend anterior en [`frontend/`](frontend/) | `web/` es el destino de la migración y está desplegado en el proyecto Pages de staging; el dominio propio de staging figura pendiente. Producción sigue sirviendo `frontend/` como rollback hasta el cambio manual descrito en [`web/README.md`](web/README.md) |
| API | FastAPI + PostgreSQL en [`backend/`](backend/) | API pública `/api/v1`, health checks y controles de producción documentados |
| Gestión | React/Vite en [`admin/`](admin/) y API `/api/admin/v1` | Fases 1–4 implementadas para artesanos, piezas, auditoría y media; el acceso operativo depende de Cloudflare Access y de configuración externa |
| Certificados y NFC | Modelos, servicio, resolución privada y CLI de provisioning | Implementados; certificados y etiquetas se operan por CLI, fuera de Gestión |
| Media | Originales privados, derivados públicos y `/media/` servido por la API cuando `MEDIA_ROOT` está configurado | Backend y Gestión implementados; la regla A permite `GET|HEAD /media/*` según la verificación externa del 2026-09-30. El soporte M3 para respaldar originales está en el código, sin registro versionado de activación |
| Backups | D10 cifrado con `age`, copia fuera del host y restore drill | [`docs/BACKUP.md`](docs/BACKUP.md) registra D10.1–D10.3 y B2 activos/verificados para la base; esos registros son evidencia operativa fechada |

PostgreSQL es la fuente de verdad para artesanos, piezas, certificados, NFC y
metadatos de media; los archivos viven bajo `MEDIA_ROOT`. El contenido real se
administra fuera del repositorio. Los archivos versionados no prueban qué
registros o archivos existen hoy ni su estado de publicación.

## Fuentes de verdad

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): topología vigente e historia
  de migración.
- [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md): contratos público y de
  Gestión.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md): modelo persistente.
- [`docs/SECURITY.md`](docs/SECURITY.md): requisitos y controles de seguridad.
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md): topología y controles operativos
  verificados.
- [`docs/MEDIA.md`](docs/MEDIA.md) y [`docs/BACKUP.md`](docs/BACKUP.md): media,
  D10 y recuperación.
- [`docs/DECISIONS.md`](docs/DECISIONS.md): decisiones arquitectónicas.

## Superficies principales

```text
artesanfc.com
  frontend/ en producción (estado documentado 2026-09-30)

artesanfc-staging.pages.dev
  web/ desplegado en el proyecto de staging

api.artesanfc.com
  /api/v1/*                  API pública
  /media/*                   derivados públicos (GET/HEAD)

gestion.artesanfc.com
  admin/                     interfaz de Gestión
  /api/admin/v1/*            API administrativa protegida por Access
```

La API pública expone el catálogo y resuelve certificados mediante
`POST /api/v1/certificates/resolve`. La emisión, rotación, revocación y
programación NFC se realizan con la CLI descrita en
[`docs/PROVISIONING.md`](docs/PROVISIONING.md).

## Estructura del repositorio

```text
artesa-nfc/
├── web/             Frontend público Astro; migración y staging
├── frontend/        Frontend anterior; producción y rollback documentados
├── admin/           Gestión (React/Vite)
├── backend/         FastAPI, PostgreSQL, migraciones y herramientas operativas
├── docs/            Arquitectura, contratos, seguridad y runbooks
├── qa/              QA reproducible y utilidades de inspección de solo lectura
└── .github/         CI de backend, web, Gestión y releases
```

`qa/db/legacy-inventory.sql`, añadido por #134, inspecciona esquema,
estadísticas y relaciones de tablas legacy dentro de una transacción
`READ ONLY`; no prueba que la limpieza de esas tablas se haya ejecutado. La base
auditada antes de crear esta rama fue `origin/develop@8368c51`, sincronizada y
con un único cambio de contenido respecto de `origin/main@6e59508`: el
inventario de QA incorporado por #168.

## Desarrollo y validación

- Backend: [`backend/README.md`](backend/README.md)
- Frontend Astro: [`web/README.md`](web/README.md)
- Gestión: [`admin/README.md`](admin/README.md)
- QA del certificado privado:
  [`docs/QA_PRIVATE_ROUTE.md`](docs/QA_PRIVATE_ROUTE.md)
- Despliegue y recuperación:
  [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) y
  [`docs/BACKUP.md`](docs/BACKUP.md)

Los cierres de Sprint 0–4 en `docs/SPRINT_*.md` conservan la historia del
proyecto. Describen el estado de cada corte y no sustituyen este resumen ni los
documentos vigentes anteriores.
