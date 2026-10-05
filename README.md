# ArtesaNFC

Plataforma para documentar y autenticar piezas artesanales de Oaxaca mediante
un catálogo público, Gestión y certificados vinculados a etiquetas NFC.

## Estado actual

Este resumen refleja el código en `develop` al 2026-10-05. El estado operativo
externo se toma de los runbooks fechados; el repositorio no permite comprobar
por sí solo el contenido ni la configuración viva de producción.

| Área | Implementación en el repositorio | Estado documentado |
|---|---|---|
| Sitio público | Astro estático en [`web/`](web/) y frontend anterior en [`frontend/`](frontend/) | `web/` sirve `artesanfc.com` desde el 2026-10-03 (última publicación: `main` `ab85ef2`, 2026-10-05) y `staging.artesanfc.com` desde `develop`. `frontend/` queda solo como rollback ([`web/README.md`](web/README.md)) |
| API | FastAPI + PostgreSQL en [`backend/`](backend/) | API pública `/api/v1`, health checks y controles de producción documentados |
| Gestión | React/Vite en [`admin/`](admin/) y API `/api/admin/v1` | Fases 1–4 (artesanos, piezas, auditoría, media), Certificación v2 (ADR-030: roles, certificación y grabado NFC, tarjeta del comprador, paleta y diseño del certificado), ventas y precio, autorización del artesano y cuentas (P-026). El acceso depende de Cloudflare Access y de configuración externa |
| Certificados y NFC | Modelos, servicio, resolución privada y CLI de provisioning | Implementados. Los Custodios los operan desde Gestión con Web NFC (Chrome para Android, ADR-030); la CLI de provisioning sigue como alternativa |
| Media | Originales privados, derivados públicos y `/media/` servido por la API cuando `MEDIA_ROOT` está configurado | Backend y Gestión implementados; la regla A permite `GET|HEAD /media/*` según la verificación externa del 2026-09-30. El soporte M3 para respaldar originales está en el código, sin registro versionado de activación |
| Despliegue | Releases en GitHub y `artesa-deploy` (TOOL 1.9.0) | `fetch` → `prepare` → `deploy` → `ui` ([`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) §11.8); el primero así fue R26 (2026-10-05) |
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
  web/ (Astro) en producción desde 2026-10-03
  /c/<token>                 certificado privado (enlace del tag NFC)

staging.artesanfc.com
  web/ de develop (proyecto Pages artesanfc-staging, noindex)

api.artesanfc.com
  /api/v1/*                  API pública
  /media/*                   derivados públicos (GET/HEAD)

gestion.artesanfc.com
  admin/                     interfaz de Gestión
  /api/admin/v1/*            API administrativa protegida por Access
```

La API pública expone el catálogo y resuelve certificados mediante
`POST /api/v1/certificates/resolve`. La emisión, rotación, revocación y
programación NFC se hacen desde Gestión → Certificación (solo Custodios, Web NFC);
la CLI descrita en [`docs/PROVISIONING.md`](docs/PROVISIONING.md) sigue
disponible.

## Estructura del repositorio

```text
artesa-nfc/
├── web/             Frontend público Astro; producción y staging
├── frontend/        Frontend anterior; solo rollback
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
