# Sprint 3 — Backend y API pública — Cierre

**Estado:** COMPLETED (backend y API pública; ver sección 12 sobre frontend)
**Fecha de referencia:** 2026-09-17
**Alcance:** construir el backend de ArtesaNFC (FastAPI + PostgreSQL) y su
API pública de solo lectura para artesano y pieza, siguiendo
`docs/DATA_MODEL.md`, `docs/API_CONTRACT.md` y `docs/SECURITY.md`, sobre
la base ya cerrada en Sprint 2 (`docs/SPRINT_2.md`).

Este documento cierra formalmente Sprint 3. Marca la finalización del
sprint de **Backend y API pública**, no del MVP completo de ArtesaNFC.
Certificados, NFC, ownership y `audit_event` quedan explícitamente fuera
(sección 11) y son alcance de Sprint 4 (`docs/WORKFLOW.md` §14). La
integración del frontend de Sprint 2 con esta API también queda fuera de
este cierre (sección 12).

**Issue de seguimiento:** #39 — Sprint 3 — Backend and public API. Esta
Issue **no se marca como cerrada** en este documento hasta que la
verificación final (sección 9) pase; ver sección 9 para el resultado real
de esa verificación, obtenido en este mismo cierre.

## 1. Objetivo

Construir el backend FastAPI + PostgreSQL de ArtesaNFC: fundación de la
aplicación, modelos y migraciones, datos demo deterministas, y la API
pública de solo lectura de artesano y pieza (`GET /api/v1/artisans`,
`GET /api/v1/artisans/{slug}`, `GET /api/v1/pieces`,
`GET /api/v1/pieces/{slug}`), consistente con `docs/API_CONTRACT.md` y
sin adelantar ningún comportamiento de certificado/NFC de Sprint 4.

## 2. Issues completadas en Sprint 3

| Issue | Alcance | PR |
|---|---|---|
| **#40** | Backend foundation and config — esqueleto FastAPI, `Settings`, `/health`, manejo de errores públicos. | #41 |
| **#42** | PostgreSQL models and Alembic migrations — `Artisan`, `Piece`, `MediaAsset`, migración inicial reversible. | #43 |
| **#44** | Seed reproducible demo fixtures — 3 artesanos, 4 piezas, 7 medios, deterministas e idempotentes. | #45 |
| **#46** | Public artisans API — `GET /api/v1/artisans`, `GET /api/v1/artisans/{slug}`. | #47 |
| **#48** | Public pieces API — `GET /api/v1/pieces`, `GET /api/v1/pieces/{slug}`. | #49 |

Issue de seguimiento del sprint completo: **#39** — Sprint 3 — Backend and
public API (ver nota sobre su cierre en la cabecera de este documento).

Corrección de cierre incluida en esta revisión (auditoría de solo
lectura previa a este cierre): tres hallazgos de consistencia
documental/prueba, sin cambios a modelos, migraciones ni fixtures de
seed:

- `app/core/errors.py`: los errores 404/405 generados directamente por
  el framework (ruta no encontrada, método no permitido) usaban la
  frase por defecto de Starlette (`"Not Found"`, `"Method Not Allowed"`)
  en vez del mensaje público canónico ya definido en
  `_DEFAULT_MESSAGES`. Corregido para que ambos casos usen el mismo
  mensaje que ya usaban los errores 404 generados por la propia
  aplicación (`not_found()`); el `code` ya era correcto en ambos casos y
  no cambia.
- `app/core/config.py`: `Settings` rechazaba con `ValidationError`
  cualquier `.env` real copiado de `.env.example`, porque
  `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`/`POSTGRES_HOST`/
  `POSTGRES_PORT` (usadas directamente por `docker-compose.yml` para el
  servicio `db`) no estaban declaradas como campos de `Settings` y el
  modelo no permitía campos adicionales. Esto habría roto el arranque
  del contenedor `api` (que carga `.env` vía `env_file`) y el flujo de
  desarrollo local documentado en `backend/README.md` la primera vez que
  alguien copiara `.env.example` a `.env`. Corregido agregando
  `extra="ignore"` a `SettingsConfigDict`; `DATABASE_URL` (el único
  valor que `Settings` necesita) no cambia de comportamiento.
- Cobertura de pruebas ampliada con `backend/tests/test_error_envelopes.py`
  (sección 9).

## 3. Alcance completado

- Fundación de la aplicación FastAPI (`app/main.py`, `app/core/config.py`,
  `app/core/errors.py`) con manejadores de excepción registrados para
  `HTTPException`, `RequestValidationError` y errores no controlados.
- Endpoint `GET /health`, con verificación real de conectividad a
  PostgreSQL (`app/db/session.py::check_database_connection`).
- Modelos SQLAlchemy `Artisan`, `Piece`, `MediaAsset`
  (`app/models/`), consistentes campo a campo con `docs/DATA_MODEL.md`
  §2.1/§2.2/§2.5, incluyendo los `CHECK`/FK/índices documentados.
- Una migración Alembic inicial (`9229b1f0f11c`), reversible.
- Seed determinista e idempotente (`app/db/seed.py`): 3 artesanos, 4
  piezas, 7 medios, todos explícitamente ficticios.
- API pública de artesano y pieza (`app/api/v1/artisans.py`,
  `app/api/v1/pieces.py`), con los schemas Pydantic de
  `app/schemas/` reflejando `docs/API_CONTRACT.md` §4-§6 sin campos
  extra ni faltantes.
- Modelo de errores públicos consistente con `docs/API_CONTRACT.md`
  §10 (404, 422, 405, 500), con mensaje canónico unificado (sección 2).
- Suite de pruebas (`backend/tests/`, sección 9).

## 4. Modelo de datos implementado

Tablas creadas por la migración `9229b1f0f11c`: `artisan`, `piece`,
`media_asset` (más `alembic_version`, gestionada por Alembic). Ninguna
otra tabla existe en este esquema.

Restricciones verificadas en el modelo y en la migración, consistentes
con `docs/DATA_MODEL.md`:

- `piece.artisan_id → artisan.id` con `ON DELETE RESTRICT` (§5).
- `media_asset.artisan_id`/`media_asset.piece_id → … ON DELETE SET NULL`
  (§5), con `CHECK (num_nonnulls(artisan_id, piece_id) <= 1)` (§4,
  restricción D).
- `CHECK` adicional en `media_asset` (no explícito en `DATA_MODEL.md`
  §4, agregado en la implementación como refuerzo del requisito de
  `API_CONTRACT.md` §6 de que `alt_text` es obligatorio para `type =
  image`): `media_type != 'image' OR alt_text IS NOT NULL`.
- Unique constraints: `artisan.slug`, `piece.slug`, `piece.public_code`
  (§6).
- Índices: FKs, `publication_status` en `artisan`/`piece`, y el índice
  compuesto `(artisan_id, piece_id, role, position)` en `media_asset`
  (§7).

**Reversibilidad verificada en este cierre** (sección 9): `alembic
downgrade base` elimina las tres tablas y los cinco tipos `ENUM` nativos
de Postgres que crea la migración, sin dejar tipos huérfanos; `alembic
upgrade head` reconstruye el esquema completo de forma idéntica.

## 5. Seed de datos demo

`app/db/seed.py` produce exactamente:

- 3 artesanos (`artisan-demo-01`, `artesana-demo-02`, `artisan-demo-03`).
- 4 piezas (`mascara-demo-01`, `figura-tallada-demo-01`,
  `textil-demo-01`, `vasija-demo-01`).
- 7 medios (`portrait` por artesano, `hero` por pieza).

Propiedades verificadas (código y pruebas, `tests/test_seed.py`, y de
nuevo en la verificación final de este cierre, sección 9):

- **IDs deterministas**: `uuid.uuid5(SEED_NAMESPACE, <key>)`;
  `uuid.uuid4()` nunca se usa para estas filas.
- **Idempotencia**: correr el seed dos veces produce los mismos 3/4/7
  registros, sin duplicados.
- **Manejo de colisión**: si un `slug`/`public_code` aprobado ya existe
  bajo un `id` distinto al esperado, el seed completo aborta con
  `SeedCollisionError` y revierte (transacción única), sin adoptar ni
  sobrescribir el registro ajeno.
- **Contenido explícitamente ficticio**: todos los nombres, biografías y
  descripciones se identifican como "ficticio de demostración"; ningún
  dato representa a un artesano o pieza real de `docs/PROJECT.md` §11.

## 6. API pública implementada

```text
GET /api/v1/artisans
GET /api/v1/artisans/{slug}
GET /api/v1/pieces
GET /api/v1/pieces/{slug}
```

Verificado contra `docs/API_CONTRACT.md`:

- Envoltura de listado `{"data": [...], "meta": {"total": N}}` (§8).
- Resúmenes de listado (`ArtisanSummary`, `ArtisanPieceSummary`) usan
  exactamente los campos documentados, sin campos extra (§4-§5, §8).
- Invariante de publicación pieza+artesano (§9): aplicado a nivel de
  consulta SQL (`_visible_pieces_query`, join sobre `Artisan`), no como
  post-filtro — una pieza `published` bajo un artesano no publicado
  nunca aparece en listado ni detalle, y su detalle responde el mismo
  404 genérico que un slug inexistente.
- `languages` siempre presente como array; contenido real solo si
  `languages_public = true` (§4).
- `techniques`/`materials`/`media` siempre arrays, nunca `null` (§4,
  §5, §12).
- Filtros documentados en `GET /api/v1/pieces` (`artisan`,
  `availability_status`); `availability_status` inválido produce `422`
  estándar de FastAPI/Pydantic (§8, §10).
- Ningún campo prohibido (`has_certificate`, `certificate_id`,
  `certificate_status`, `has_nfc`, cualquier dato de `nfc_tag`, UUIDs
  internos, `publication_status`, `storage_path` como clave) aparece en
  ninguna respuesta (§5, §11) — verificado por aserciones negativas
  explícitas en las pruebas (sección 9).

## 7. Modelo de errores públicos

Envoltura estándar (`docs/API_CONTRACT.md` §10):

```json
{ "error": { "code": "...", "message": "..." } }
```

| Código | Origen | `code` | Verificado |
|---|---|---|---|
| `404` | `not_found()` (slug desconocido/no publicado) | `not_found` | Sí, pruebas de artisans/pieces + `test_error_envelopes.py` |
| `404` | Ruta no coincidente (framework) | `not_found` | Sí, `test_error_envelopes.py` (nuevo, sección 2/9) |
| `405` | Método no soportado en ruta existente (framework) | `method_not_allowed` | Sí, `test_error_envelopes.py` (nuevo) |
| `422` | Validación de entrada (Pydantic/FastAPI) | `validation_error` | Sí, pruebas existentes de `pieces` |
| `500` | Excepción no controlada | `internal_error` | Sí, `test_error_envelopes.py` (nuevo, vía override de dependencia) |

Los casos 404/405 generados directamente por el framework ahora
reutilizan el mismo mensaje canónico que los generados por la
aplicación (corrección de la sección 2); el `code` no cambió porque ya
era correcto en ambos casos.

`429` (rate limiting) está documentado en `docs/SECURITY.md` §5 pero
delegado a Nginx/infraestructura (`docs/ARCHITECTURE.md` §3); no se
implementa en la capa de aplicación en Sprint 3, consistente con esa
delegación — no es una omisión de este cierre.

## 8. Seguridad / exposición de datos

Verificado sin excepciones, tanto por lectura de código como por
pruebas explícitas (`test_*_no_internal_fields_leak`,
`test_get_piece_detail_no_internal_fields_leak`):

- Ningún UUID interno (`artisan.id`, `piece.id`, `media_asset.id`) se
  serializa en ninguna respuesta pública.
- `publication_status`, `created_at`, `updated_at`, `artisan_id` (como
  clave separada) no aparecen en ninguna respuesta pública.
- `has_certificate`, `certificate_id`, `certificate_status`, `has_nfc`,
  y cualquier dato de `nfc_tag` no aparecen — no podrían, porque estas
  entidades no existen en este esquema (sección 11).
- `languages` respeta `languages_public` (sección 6).
- Medios `archived` (`MediaAssetStatus.archived`) se excluyen de toda
  respuesta pública.

**Decisión de mapeo de `url` de media (Sprint 3, no definitiva):** ver
`docs/API_CONTRACT.md` §6.1, agregada en este cierre. Resumen: mientras
no exista una capa real de storage/CDN, `url = "/media/{storage_path}"`
— el valor de `storage_path` es recuperable a partir de `url` en Sprint
3 (aunque nunca se expone como clave JSON propia), lo cual se acepta
explícitamente porque todo el contenido servido hoy es el fixture
ficticio de la sección 5, no datos reales de artesano/pieza. Antes de
servir contenido real se espera reemplazar este mapeo por una capa de
media/CDN real, sin cambiar el contrato de la columna `storage_path` en
`docs/DATA_MODEL.md` ni la forma pública de `MEDIA_ASSET`.

## 9. Validación / pruebas

### 9.1 Suite automatizada

`backend/tests/`: `test_health.py`, `test_models_schema.py`,
`test_seed.py`, `test_api_artisans.py`, `test_api_pieces.py`, y el
archivo nuevo de este cierre `test_error_envelopes.py` (cobertura de
404 no coincidente, 405, y 500 genérico vía override de dependencia —
sección 2/7).

### 9.2 Verificación final contra PostgreSQL real (este cierre, 2026-09-17)

Ejecutada íntegramente como parte de este cierre, contra una base de
datos limpia levantada con `docker compose up -d db`:

| Paso | Resultado |
|---|---|
| `alembic upgrade head` (DB limpia) | OK — crea `artisan`, `piece`, `media_asset` (+ `alembic_version`); ninguna tabla de Sprint 4. |
| `alembic downgrade base` | OK — elimina las 3 tablas y los 5 tipos `ENUM` nativos, sin huérfanos. |
| `alembic upgrade head` (segunda vez) | OK — reconstruye el esquema completo de forma idéntica. |
| `python -m app.db.seed` × 2 | OK — ambas corridas reportan `3 artisans, 4 pieces, 7 media assets`; conteos en DB idénticos tras la segunda corrida. |
| `pytest -q` | **60 passed** (57 preexistentes + 3 nuevas de `test_error_envelopes.py`), 0 fallos. |
| `GET /health` | `{"status": "ok", "database": "connected"}` |
| `GET /api/v1/artisans` | `200`, 3 artesanos, envoltura `data`/`meta` correcta. |
| `GET /api/v1/artisans/artisan-demo-01` | `200` |
| `GET /api/v1/pieces` | `200`, 4 piezas. |
| `GET /api/v1/pieces/mascara-demo-01` | `200` |
| `GET /api/v1/does-not-exist` | `404`, `{"error":{"code":"not_found","message":"The requested resource does not exist."}}` |
| `DELETE /api/v1/pieces` | `405`, `{"error":{"code":"method_not_allowed","message":"This method is not allowed for this resource."}}` |
| Tablas de Sprint 4 (`certificate`, `nfc_tag`, `audit_event`) | Ausentes — confirmado por inspección directa del esquema. |
| Limpieza (`docker compose down -v`) | OK — sin contenedores, volúmenes ni red de `backend` remanentes; `.env` de verificación eliminado (no existía antes de este cierre). |

Esta verificación fue posible solo tras la corrección de
`app/core/config.py` descrita en la sección 2 — sin ella, cualquier
`.env` real (incluido el que usa el contenedor `api` vía
`docker-compose.yml`) hacía fallar `Settings()` al arrancar.

## 10. Flujo de desarrollo local

Documentado en `backend/README.md` (nuevo en este cierre): variables de
entorno, `docker compose up -d db`, entorno Python local, `alembic
upgrade head`, `python -m app.db.seed`, `uvicorn app.main:app --reload`,
`pytest -q`. Incluye la aclaración de que las migraciones y el seed se
ejecutan desde el entorno Python del host, no desde la imagen Docker
(la imagen no incluye `alembic/` ni ejecuta migraciones al arrancar) —
decisión intencional para Sprint 3, no una omisión, y explícitamente no
una afirmación de preparación para producción.

## 11. Explícitamente diferido a Sprint 4

Ninguno de los siguientes existe en este esquema, código o pruebas —
verificado en este cierre y reforzado por pruebas dedicadas
(`test_models_schema.py::test_migration_head_creates_only_expected_tables`,
`test_seed.py::test_no_certificate_nfc_tag_audit_event_data_or_models_introduced`):

- Tabla/modelo `certificate`.
- Tabla/modelo `nfc_tag`.
- Tabla/modelo `audit_event`.
- `POST /api/v1/certificates/resolve`.
- Cualquier lógica de token, hash de token, revocación o rate limiting
  de certificado.
- Ownership/transferencia de propiedad.
- API administrativa (`/api/v1/admin/...`) — permanece puramente
  conceptual, sin ningún endpoint implementado.

Esto sigue `docs/WORKFLOW.md` §14: Sprint 4 es "flujo NFC/certificado
privado".

## 12. Frontend — decisión explícita de alcance

El frontend estático de Sprint 2 (`frontend/`) **no cambia** en este
cierre y sigue siendo completamente funcional con su contenido fixture
propio; no se modificó ningún archivo de `frontend/` como parte de
Sprint 3.

**Decisión de esta cierre, resolviendo la ambigüedad documental
señalada en la auditoría previa a este cierre:** conectar el frontend
de Sprint 2 a esta API pública queda **diferido a un Issue de
seguimiento separado**, a resolver antes del lanzamiento a producción,
no como parte del cierre de Sprint 3 backend/API pública. Esto **no**
es trabajo de Sprint 4 (que es NFC/certificado, sección 11) — es un
paso de integración intermedio, propio de la capa frontend
(`docs/PROJECT.md` §12, ChatGPT), entre "la API pública existe" (este
cierre) y "el sitio público consume datos reales" (fuera de este
cierre).

## 13. Notas conocidas no bloqueantes

- `docs/SECURITY.md` §20 actualizado en este cierre: el
  `CROSS-DOCUMENT CHANGE REQUIRED` sobre reemisión de certificado
  (histórico, referido a Sprint 4) ya estaba resuelto por
  `docs/DATA_MODEL.md` §14 desde el 2026-09-16, pero `SECURITY.md` no
  reflejaba esa resolución hasta ahora.
- `docs/API_CONTRACT.md` §6.1 (nuevo): documenta explícitamente el
  mapeo temporal de `url` de media (sección 8) como decisión de Sprint
  3, no definitiva.
- `README.md` raíz actualizado en este cierre: ya no describe la
  arquitectura legada de Cloudflare D1/Workers como la implementación
  activa; los archivos `public/`, `src/`, `db/`, `wrangler.toml`
  permanecen en el repositorio únicamente como estado de migración
  (`docs/ARCHITECTURE.md` §1/§15), sin cambios de contenido en este
  cierre.
- El mecanismo de rate limiting (`docs/SECURITY.md` §5) permanece sin
  implementar a nivel de aplicación, delegado a la capa de
  Nginx/infraestructura aún no desplegada — consistente con
  `docs/ARCHITECTURE.md` §3, no un defecto de este cierre.

## 14. Listo para la siguiente fase

Criterios de entrada verificados:

- [x] Backend FastAPI + PostgreSQL funcional, con `/health` verificando
      conectividad real.
- [x] Modelos y migración consistentes con `docs/DATA_MODEL.md`,
      migración reversible verificada contra PostgreSQL real.
- [x] Seed determinista, idempotente y con manejo de colisión,
      verificado contra PostgreSQL real (dos corridas).
- [x] API pública de artesano y pieza consistente con
      `docs/API_CONTRACT.md`, sin campos prohibidos.
- [x] Modelo de errores públicos consistente en los cinco casos
      documentados (404 aplicación, 404 framework, 405, 422, 500).
- [x] 60/60 pruebas automatizadas pasan contra PostgreSQL real.
- [x] Ninguna tabla/modelo/endpoint de Sprint 4 presente.
- [x] Frontend de Sprint 2 sin cambios y sin dependencia de esta API
      todavía (sección 12).
- [x] Flujo de desarrollo local documentado y verificado
      (`backend/README.md`).
- [x] Limpieza completa de la verificación final (sin contenedores,
      volúmenes, red ni `.env` remanentes).
- [x] Sin `PROPOSED DECISION` abierto que bloquee la siguiente fase.
- [x] Sin `CROSS-DOCUMENT CHANGE REQUIRED` abierto (sección 13).

Sprint 3 está listo para la siguiente fase planeada en la secuencia de
`docs/WORKFLOW.md` §14 (Sprint 4 — flujo NFC/certificado privado), en
paralelo con el Issue de integración frontend descrito en la sección
12. Este documento no define ni anticipa el alcance detallado de esas
fases — eso se documentará en sus propios cierres, cuando corresponda.

## 15. Resultado de Sprint 3

ArtesaNFC cuenta ahora con un backend FastAPI + PostgreSQL real,
migrado con Alembic de forma reversible, poblado con datos demo
deterministas e idempotentes, y sirviendo una API pública de artesano y
pieza que respeta field-por-field el contrato ya aprobado en
`docs/API_CONTRACT.md`, sin ninguna fuga de dato interno o de Sprint 4.
La verificación final de este cierre (sección 9.2) corrió la secuencia
completa contra una base de datos PostgreSQL real —no solo contra los
mocks/fixtures de prueba— y confirmó 60/60 pruebas verdes, migración
reversible, seed idempotente y los cinco casos del modelo de errores
público. No quedan `PROPOSED DECISION` ni `CROSS-DOCUMENT CHANGE
REQUIRED` abiertos.
