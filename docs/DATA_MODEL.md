# ArtesaNFC — Modelo de Datos

**Estado:** Aprobado para MVP
**Issue:** #3 — docs: create DATA_MODEL.md
**Dominio de referencia:** `artesanfc.com`
**Fecha de referencia:** 2026-09-16

## 0. Relación con otros documentos

Este documento traduce a modelo de datos lo ya aceptado en `docs/PROJECT.md`,
`docs/ARCHITECTURE.md` y `docs/DECISIONS.md`, más las decisiones tomadas por
Alexis (Product Owner) sobre esta propuesta. No reabre decisiones ya
aprobadas (ADR-001 a ADR-025); cuando un detalle sigue sin resolver, se
marca explícitamente como `PROPOSED DECISION`.

Fuera de alcance de este documento (pertenecen a otros documentos, no se
deciden aquí):

- **`SECURITY.md`**: algoritmo exacto de hashing del token de certificado,
  implementación detallada de rate limiting, autenticación administrativa
  detallada.
- **`API_CONTRACT.md`**: convenciones de serialización de la API (nombres
  de campo en JSON, formato de respuestas). Este documento solo fija la
  convención de nombres a nivel de base de datos/modelo interno
  (`snake_case`); no congela cómo se expone en la API.
- **Migración de datos legado**: la estrategia completa de migración desde
  `docs/diccionario-datos.md`/`db/schema.sql` hacia este modelo es una
  tarea/documento futuro, no se define aquí (ver sección 0.1).

### 0.1 Nota sobre el esquema legado

`docs/diccionario-datos.md` documenta el esquema actual de Cloudflare D1,
donde el `id` público de `piezas` funciona a la vez como identificador de
certificado con formato `REGION-AÑO-TIPO-CONSECUTIVO` (secuencial y
predecible). Este modelo nuevo **no hereda ese diseño**: ese identificador
secuencial nunca debe convertirse en el secreto del certificado privado
(contradice ADR-007 y ADR-008). Un `public_code` legible y no secreto
puede seguir existiendo de forma independiente (sección 2.2), pero no
sustituye al token del certificado. La estrategia concreta de migración de
datos queda fuera de este documento.

## 1. Convenciones generales

- Todas las tablas usan `id` interno tipo `UUID` como clave primaria,
  generado como **UUIDv4** (aprobado).
- La convención de nombres de tabla y columna es **`snake_case`**
  (aprobado), tanto para el esquema de base de datos como para los
  atributos de los modelos internos. La forma en que estos nombres se
  serializan hacia la API pública es responsabilidad de
  `API_CONTRACT.md` y no se fija en este documento.
- Identificadores públicos (`slug`, `public_code`) son distintos del `id`
  interno y nunca se usan como clave primaria.
- Todas las tablas incluyen `created_at` y `updated_at` en UTC
  (`timestamptz`), salvo `audit_event` (ver sección 11).
- Las entidades núcleo prefieren cambios de estado/archivado sobre borrado
  físico destructivo (aprobado). El borrado físico solo aplica donde se
  indique explícitamente.

## 2. Entidades

### 2.1 ARTISAN

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `id` | UUID (PK, v4) | Sí | Interno, no se expone en URLs. |
| `slug` | text, unique | Sí | Público, usado en `/artesanos/{slug}`. |
| `full_name` | text | Sí | Nombre completo. |
| `artistic_name` | text | No | Nombre artístico, si difiere del nombre completo. |
| `locality` | text | No | Localidad/pueblo. |
| `municipality` | text | No | Municipio. |
| `state` | text | No, default `Oaxaca` | Aprobado. |
| `country` | text | No, default `México` | Aprobado. |
| `languages` | text[] o jsonb | No | Lenguas indígenas habladas. Opcional; público solo cuando sea apropiado/autorizado. |
| `languages_public` | boolean | Sí, default `false` | Controla si `languages` se expone en las rutas públicas, para cumplir la condición de autorización. |
| `biography` | text | No | Reseña breve. |
| `history` | text | No | Historia extendida (narrativa editorial). |
| `techniques` | text[] o jsonb | No | Colección simple de técnicas; sin catálogo normalizado en el MVP (aprobado). |
| `public_contact` | jsonb | No | Redes/contacto público (ej. Instagram, sitio web). |
| `publication_status` | enum (`draft`\|`published`\|`archived`) | Sí, default `draft` | Ver sección 9. |
| `created_at` / `updated_at` | timestamptz | Sí | — |

La fotografía de perfil/portada del artesano se maneja como `MEDIA_ASSET`
con `artisan_id` asignado y `role = portrait` o `hero`, no como columna
dedicada en esta tabla (aprobado: no se agrega `cover_media_id`).

### 2.2 PIECE

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `id` | UUID (PK, v4) | Sí | Interno. |
| `slug` | text, unique | Sí | Público, usado en `/piezas/{slug}`. |
| `public_code` | text, unique | Sí | Código público legible de referencia de la pieza. Independiente del token de certificado y del `id` interno (ADR-008); puede coexistir con el identificador legado como valor legible, pero nunca actúa como secreto. |
| `artisan_id` | UUID (FK → artisan.id) | Sí | Una pieza pertenece a un solo artesano en el MVP (ADR-004). |
| `name` | text | Sí | Nombre comercial/editorial de la pieza. |
| `description` | text | No | Descripción corta. |
| `history` | text | No | Historia/narrativa extendida. |
| `technique` | text | No | Técnica principal empleada. |
| `materials` | text[] o jsonb | No | Colección simple de materiales; sin catálogo normalizado en el MVP (aprobado). |
| `origin` | text | No | Origen geográfico/cultural de la pieza. |
| `creation_year` | smallint | No | Campo común para año de creación (aprobado). |
| `creation_date` | date | No | Solo cuando se conoce la fecha exacta (aprobado, opcional). |
| `dimensions` | jsonb | No | JSON estructurado: `{ "height": <number>, "width": <number>, "depth": <number>, "unit": "mm"\|"cm"\|"in" }`, con campos ausentes cuando no aplican (aprobado). |
| `visual_theme` | jsonb | No | Metadatos flexibles de presentación propia de la pieza (ADR-012), esquema abierto por diseño (aprobado). |
| `availability_status` | enum (`available`\|`reserved`\|`exhibited`\|`archived`) | Sí, default `available` | Estado de la pieza en sí, separado de `publication_status` (aprobado). |
| `publication_status` | enum (`draft`\|`published`\|`archived`) | Sí, default `draft` | Ver sección 9. |
| `created_at` / `updated_at` | timestamptz | Sí | — |

Multimedia (fotografía, video, 3D/360) se maneja vía `MEDIA_ASSET`, no como
columnas en `PIECE`.

### 2.3 CERTIFICATE

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `id` | UUID (PK, v4) | Sí | Interno. |
| `piece_id` | UUID (FK → piece.id) | Sí | **Ya no es globalmente unique.** Una pieza puede tener **múltiples registros históricos** de `CERTIFICATE` a lo largo del tiempo (reemisión/reemplazo, `SECURITY.md` §14.1), pero como máximo **uno** de esos registros puede tener `status = 'active'` para una pieza dada en cualquier momento (ver restricción C', sección 4, y unique constraints, sección 6). |
| `token_hash` | text, unique, nullable | Condicional | Hash del token privado. Nulo mientras `status = draft` (el token aún no se ha emitido); obligatorio cuando `status = active` o `status = revoked`. **El token en texto plano nunca se persiste.** |
| `issued_at` | timestamptz | No | Fecha de emisión; nulo mientras el certificado está en `draft`. |
| `status` | enum (`draft`\|`active`\|`revoked`) | Sí | `draft`: registro creado pero token aún no emitido/activado (`token_hash` nulo). `active`: token emitido, hasheado y resoluble (`token_hash` obligatorio). `revoked`: invalidado (`token_hash` se conserva; ver regla de revocación abajo). |
| `revoked_at` | timestamptz | Condicional | Obligatorio (no nulo) cuando `status = revoked`; normalmente nulo en cualquier otro estado. |
| `revocation_reason` | text | No | Motivo de revocación. Sigue siendo opcional; `SECURITY.md` puede endurecer esta regla más adelante. |
| `version` | integer | Sí, default `1` | Versión del esquema de `authenticity_metadata`, para permitir evolución sin migrar certificados antiguos. |
| `authenticity_metadata` | jsonb | No | JSONB versionable (aprobado); el campo `version` indica qué forma tiene este JSON en cada certificado. |
| `created_at` / `updated_at` | timestamptz | Sí | — |

Reglas de integridad conceptuales:

- **Emisión de token:** `CHECK ((status = 'draft' AND token_hash IS NULL) OR (status IN ('active', 'revoked') AND token_hash IS NOT NULL))`.
  Evita el conflicto entre "`token_hash` obligatorio" y "`draft` = token aún no emitido": mientras no se emite el token, el certificado permanece en `draft` sin `token_hash`; en cuanto se emite, pasa a `active` con `token_hash` ya presente y ese valor no se limpia al revocar.
- **Revocación:** `CHECK ((status = 'revoked' AND revoked_at IS NOT NULL) OR (status != 'revoked' AND revoked_at IS NULL))`.
  Un certificado revocado siempre tiene `revoked_at`; en cualquier otro estado, `revoked_at` permanece nulo.
- **Como máximo un certificado activo por pieza (nuevo, resuelve
  `CROSS-DOCUMENT CHANGE REQUIRED` de `SECURITY.md` §14.1/§20):** unique
  index **parcial** conceptualmente equivalente a
  `UNIQUE(piece_id) WHERE status = 'active'`. Permite múltiples filas
  históricas de `CERTIFICATE` para la misma `piece_id` (ej. `revoked`),
  pero como máximo una fila con `status = 'active'` en cualquier
  momento para esa pieza — mismo patrón ya aprobado para `nfc_tag`
  (sección 2.4, restricción B).
- **Historial preservado:** un certificado `revoked` **nunca** se borra
  ni se sobrescribe al emitir un reemplazo; permanece como fila
  histórica independiente, con su propio `token_hash` (del token
  anterior, ya inválido) intacto.
- **Token de reemplazo independiente:** un certificado de reemplazo es
  una **fila nueva** de `CERTIFICATE` (no una actualización de la fila
  revocada), con un `token_hash` calculado a partir de un token
  completamente nuevo, generado de forma independiente (sección 2,
  ADR-007) — nunca derivado del token anterior. La verificación exige
  `status = 'active'` (sección 2.3 arriba, `SECURITY.md` §3.2/§14), por
  lo que un `token_hash` de una fila `revoked` nunca vuelve a producir
  `authentic`, sin importar cuántos certificados de reemplazo existan
  después para la misma pieza.

**Aprobado por Alexis (Product Owner) el 2026-09-16 — sin enlace
explícito de reemplazo en el MVP:** este documento **no** agrega una
columna que enlace explícitamente un certificado de reemplazo con el
certificado `revoked` al que sucede (ej. `replaces_certificate_id`).
Motivo: el historial de certificados ya está preservado al permitir
múltiples filas de `CERTIFICATE` por pieza (arriba); `issued_at`,
`revoked_at` y `status` ya dan suficiente historial de ciclo de vida
para el piloto actual (2 artesanos, 4 piezas); el MVP no necesita una
cadena explícita certificado-a-certificado, y sobre-diseñar esa
relación para el volumen actual no está justificado. Esto queda
**fuera de alcance del MVP**, no como decisión pendiente: una relación
de reemplazo auto-referenciada (`replaces_certificate_id` u
equivalente) puede agregarse más adelante mediante una migración
**aditiva** si surge una necesidad real de producto/auditoría — no se
anticipa aquí ni se bloquea el resto de este documento por ello.

Límites de seguridad que este documento sí fija (y no delega):

- `piece.id`, `piece.public_code`, `nfc_tag.physical_uid` y el token
  privado del certificado son **conceptos separados** (ADR-008, extendido
  en sección 10).
- Solo se persiste `token_hash`; el token en texto plano nunca se
  almacena en base de datos.

Lo que este documento **no** decide (pertenece a `SECURITY.md`):

- Algoritmo exacto de hashing del token (ej. SHA-256, HMAC, Argon2, etc.).
- Implementación detallada de rate limiting de validación de
  certificados (si vive en DB, Redis, o el reverse proxy).
- Autenticación administrativa detallada (más allá de lo indicado en
  sección 2.6 para `AUDIT_EVENT`).

### 2.4 NFC_TAG

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `id` | UUID (PK, v4) | Sí | Interno. |
| `piece_id` | UUID (FK → piece.id), nullable | No | Nulo mientras el tag no está asignado a una pieza. |
| `chip_model` | text | Sí | Ej. `NTAG213`. |
| `frequency` | text | No | Ej. `13.56 MHz`. |
| `protocol` | text | No | Ej. `ISO 14443A`. |
| `physical_uid` | text, unique (nullable-safe) | No | UID físico del chip, solo para inventario. **No es secreto** (ADR-008). |
| `programmed_at` | timestamptz | No | Cuándo se escribió la URL definitiva en el tag. |
| `locked_at` | timestamptz | No | Cuándo se bloqueó la escritura del tag. Debe ser posterior a validar la URL definitiva (ADR-021). |
| `status` | enum (`available`\|`programmed`\|`locked`\|`replaced`\|`retired`) | Sí | Aprobado. |
| `notes` | text | No | Observaciones libres (ej. incidentes de lectura). |
| `created_at` / `updated_at` | timestamptz | Sí | — |

Reglas de integridad conceptuales:

- **Asignación:** `CHECK (status NOT IN ('programmed', 'locked') OR piece_id IS NOT NULL)`.
  Un tag no puede estar `programmed` ni `locked` sin estar asignado a una pieza.
- **Bloqueo (actualizado, issue #70):** `CHECK (locked_at IS NULL OR status IN ('locked', 'replaced', 'retired'))`.
  `locked_at` solo se establece cuando el tag efectivamente alcanzó el estado `locked`, pero se preserva como metadato histórico si ese tag luego pasa a `replaced` o `retired` — no se borra al salir de `locked` (evita perder cuándo se bloqueó un tag que después fue reemplazado o dado de baja). `available` y `programmed` siguen exigiendo `locked_at IS NULL`.

El historial de NFC se preserva (aprobado): una pieza puede tener varios
registros históricos de `NFC_TAG` (por ejemplo, tags marcados como
`replaced` o `retired` tras daño o pérdida), pero **como máximo un tag
activo** (`status IN ('programmed', 'locked')`) por pieza en el MVP. Ver
restricción B en sección 4.

### 2.5 MEDIA_ASSET

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `id` | UUID (PK, v4) | Sí | Interno. |
| `artisan_id` | UUID (FK → artisan.id), nullable | No | Nunca puede estar presente al mismo tiempo que `piece_id`; puede quedar nulo junto con `piece_id` mientras el recurso está temporalmente sin dueño (aprobado: relaciones explícitas nullable, no diseño polimórfico). |
| `piece_id` | UUID (FK → piece.id), nullable | No | Ídem. |
| `media_type` | enum (`image`\|`video`\|`model_3d`\|`sequence_360`) | Sí | — |
| `role` | enum | Sí | Catálogo inicial aprobado: `hero`, `gallery`, `detail`, `process`, `portrait`, `document`, `model_3d`, `sequence_360`. Extensible sin nueva ADR si se documenta aquí. |
| `storage_path` | text | Sí | Ubicación/clave interna del recurso (nombre de columna canónico en base de datos, aprobado 2026-09-17). No es una URL pública desplegada; la URL pública se deriva más adelante en la capa de API/media a partir de este valor. |
| `alt_text` | text | Sí para `image` | Accesibilidad (`PROJECT.md` §3, responsabilidad de ChatGPT). |
| `position` | integer | Sí, default `0` | Orden de despliegue dentro del mismo dueño (`artisan_id`/`piece_id`) + `role`. |
| `format_metadata` | jsonb | No | Ej. duración de video, poligonaje de modelo 3D, número de frames de la secuencia 360. |
| `status` | enum (`active`\|`archived`) | Sí, default `active` | — |
| `created_at` / `updated_at` | timestamptz | Sí | — |

Restricción de integridad: `CHECK` que permite **como máximo uno** de
`artisan_id`/`piece_id` no nulo (nunca ambos a la vez), pero permite que
ambos sean nulos temporalmente — un recurso multimedia puede quedar sin
dueño por archivo, migración o reasignación pendiente, de forma
consistente con el borrado `SET NULL` de sección 5 (ver restricción D en
sección 4).

### 2.6 AUDIT_EVENT

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `id` | UUID (PK, v4) | Sí | Interno. |
| `occurred_at` | timestamptz | Sí | Momento del evento. |
| `actor_type` | enum (`admin_user`\|`system`) | Sí | Suficiente hasta que se diseñe autenticación (fuera del MVP). |
| `actor_id` | UUID, nullable | No | Referencia opcional al actor; sin modelo de usuario administrativo definido aún (explícitamente fuera de alcance del MVP). |
| `entity_type` | text | Sí | Ej. `certificate`, `piece`, `artisan`, `nfc_tag`. |
| `entity_id` | UUID | Sí | ID interno de la entidad afectada. |
| `action` | text | Sí | Texto con namespace, ej. `piece.created`, `certificate.revoked`. **No es un enum de base de datos** (aprobado). |
| `result` | enum (`success`\|`failure`) | Sí | — |
| `ip_address` | inet, nullable | No | Solo si es relevante para seguridad. |
| `metadata` | jsonb | No | Detalle adicional del evento. |

`AUDIT_EVENT` es **append-only / inmutable** (aprobado): no se actualiza ni
se borra, solo se inserta. No tiene `updated_at` por esta razón (sección
11). El detalle de qué eventos son obligatorios y cuánto tiempo se
retienen pertenece a `SECURITY.md`.

## 3. Extensión futura: OWNERSHIP (fuera del MVP, solo puntos de extensión)

Por ADR-010, el MVP certifica autenticidad, no propiedad. Por instrucción
explícita de Alexis, esta sección documenta **únicamente puntos de
extensión**, no un diseño completo. No se modela un sistema de propiedad
ni de transferencia de propiedad en este documento.

Puntos de extensión previstos, sin comprometer una estructura final:

- `PIECE` no tiene ninguna columna de propiedad en el MVP. Cuando se
  diseñe propiedad, se espera que se agregue mediante migración aditiva
  (columnas/tablas nuevas), sin alterar `PIECE` ni `CERTIFICATE` en su
  forma actual.
- `CERTIFICATE` permanece enfocado en autenticidad; propiedad se
  modelaría como concepto separado y relacionado, no como parte del
  certificado (mismo principio de separación de experiencias de ADR-005).
- Cualquier implementación futura de propiedad requiere su propio ADR y su
  propio documento de diseño; no se anticipa aquí el esquema de tablas.

## 4. Relaciones (MVP)

```text
ARTISAN 1 ──── N PIECE
PIECE    N ──── 1 ARTISAN
PIECE    1 ──── N CERTIFICATE histórico (máximo 1 activo, ver restricción C')
PIECE    1 ──── 0..1 NFC_TAG activo (histórico preservado, ver restricción B)
ARTISAN  1 ──── N MEDIA_ASSET (artisan_id asignado)
PIECE    1 ──── N MEDIA_ASSET (piece_id asignado)
```

**Cambio respecto a la versión anterior de este documento:** la
relación `PIECE ──── CERTIFICATE` deja de ser `0..1` en total y pasa a
ser `1..N` histórico con máximo un `active` a la vez, para resolver el
`CROSS-DOCUMENT CHANGE REQUIRED` planteado por `SECURITY.md` §14.1/§20
(reemisión de certificado sin destruir el historial revocado). Ninguna
otra relación de este diagrama cambia.

Restricciones a nivel de base de datos:

- **A.** `piece.artisan_id` es `NOT NULL` (ADR-004).
- **B.** `nfc_tag`: unique index parcial sobre `piece_id` donde
  `status IN ('programmed', 'locked')`, para garantizar como máximo un tag
  activo por pieza, preservando el resto como historial (aprobado).
- **C'.** `certificate` (**reemplaza a la restricción C anterior, que
  exigía `piece_id` unique de forma global**): `piece_id` **ya no es
  unique global**. En su lugar, unique index **parcial** sobre
  `piece_id` donde `status = 'active'`, conceptualmente equivalente a
  `UNIQUE(piece_id) WHERE status = 'active'` — como máximo un
  certificado `active` por pieza en cualquier momento, preservando el
  resto (`revoked`, y cualquier `draft` histórico) como historial. Mismo
  patrón que la restricción B para `nfc_tag`. Ver sección 2.3 para el
  detalle conceptual y sección 6 para el listado de unique constraints
  actualizado.
- **D.** `media_asset`: `CHECK (num_nonnulls(artisan_id, piece_id) <= 1)`
  — permite 0 (temporalmente sin dueño) o 1 (dueño asignado), pero nunca 2
  a la vez (aprobado: relaciones explícitas en vez de diseño polimórfico,
  consistente con el borrado `SET NULL` de sección 5).

## 5. Claves primarias y foráneas

| Tabla | PK | FK |
|---|---|---|
| `artisan` | `id` | — |
| `piece` | `id` | `artisan_id → artisan.id` |
| `certificate` | `id` | `piece_id → piece.id` |
| `nfc_tag` | `id` | `piece_id → piece.id` (nullable) |
| `media_asset` | `id` | `artisan_id → artisan.id` (nullable), `piece_id → piece.id` (nullable) |
| `audit_event` | `id` | `entity_id` (referencia lógica, no FK física — apunta a distintas tablas según `entity_type`) |

Comportamiento de borrado en FKs (aprobado):

| Relación | Comportamiento | Motivo |
|---|---|---|
| `piece.artisan_id → artisan.id` | `RESTRICT` | No se permite borrar un artesano con piezas asociadas; usar archivado/estado. |
| `certificate.piece_id → piece.id` | `RESTRICT` | El certificado forma parte de la cadena de autenticidad/historial y no debe quedar huérfano ni borrarse por accidente; revocar en vez de borrar la pieza. Aplica a **todas** las filas históricas de `CERTIFICATE` de esa pieza (activas y revocadas), no solo a la fila `active`. |
| `nfc_tag.piece_id → piece.id` | `RESTRICT` | El registro de NFC forma parte del historial de autenticidad (sección 2.4) y no debe eliminarse por cascada; desasignar se modela con `status = replaced`/`retired`, no con borrado. |
| `media_asset.piece_id → piece.id` | `SET NULL` | Un recurso multimedia puede quedar temporalmente sin pieza asociada por archivo, migración o reasignación. |
| `media_asset.artisan_id → artisan.id` | `SET NULL` | Ídem, para medios asociados a un artesano. |

Principio general: las entidades núcleo (artesano, pieza, certificado, NFC)
prefieren cambios de estado/archivado sobre borrado físico destructivo; el
`SET NULL` en `media_asset` es la única excepción, ya que un medio
desvinculado temporalmente no rompe la cadena de autenticidad.

## 6. Unique constraints

- `artisan.slug`
- `piece.slug`
- `piece.public_code`
- `certificate.token_hash` (unique cuando no es `NULL`, dado que es nulo mientras `status = draft`) — **se mantiene unique global**, sin cambios: cada `token_hash` persistido, activo o revocado, sigue siendo único en toda la tabla.
- `certificate.piece_id` **parcial, para certificados activos**:
  conceptualmente `UNIQUE(piece_id) WHERE status = 'active'` (restricción
  C', sección 4). **Ya no es unique global** — una pieza puede tener
  varias filas históricas de `CERTIFICATE` (ej. una `revoked` y una
  `active` simultáneamente), pero nunca dos filas `active` para la
  misma pieza. Mismo patrón que `nfc_tag.piece_id` abajo.
- `nfc_tag.physical_uid` (unique cuando no es `NULL`)
- `nfc_tag.piece_id` parcial para tags activos (restricción B, sección 4)

### Concurrencia del ciclo de vida (servicios de certificado y NFC)

Garantías de los servicios (`app/services/certificates.py`,
`nfc_tags.py`, `lifecycle.py`); el índice único parcial sigue siendo el
árbitro final de "un solo activo por pieza".

- **Estado persistido, no el del objeto ORM.** Cada transición bloquea la
  fila (`SELECT … FOR UPDATE`) y recarga sus columnas de ciclo de vida
  antes de validar. Un objeto desactualizado no puede sobrescribir un
  estado más nuevo ni sacar un estado terminal (`retired`, `replaced`,
  `revoked`) de su estado: falla con `LifecycleConflict`. Si el objeto
  estaba al día y la transición es inválida, el error sigue siendo el
  específico (`Invalid…Transition`).
- **Perdedor determinista de una carrera.** Dos activaciones/programaciones
  concurrentes en una pieza → el perdedor recibe `ActiveCertificateAlreadyExists` /
  `ActiveNfcTagAlreadyExists`; dos rotaciones/reemplazos → `LifecycleConflict`
  (no `…NotFound`, que queda para la ausencia real de un registro activo).
  Interbloqueo (`40P01`) y `lock_not_available` (`55P03`) también se
  reportan como `LifecycleConflict`. No se configura `lock_timeout`.
- **Transacción.** Cada transición corre en un SAVEPOINT: el conflicto se
  revierte y la sesión externa sigue usable. Los servicios nunca hacen
  `commit`; lo hace quien llama.

## 7. Índices recomendados

- Todas las FKs (`piece.artisan_id`, `certificate.piece_id`,
  `nfc_tag.piece_id`, `media_asset.artisan_id`, `media_asset.piece_id`).
- `artisan.slug`, `piece.slug`, `piece.public_code` (cubiertos por sus
  unique constraints).
- `piece.publication_status`, `artisan.publication_status` (filtrado en
  listados públicos).
- `certificate.status`.
- `certificate (piece_id, status)`: para resolver eficientemente "el
  certificado activo de esta pieza" y para listar el historial completo
  de certificados de una pieza (administración/auditoría) sin recorrer
  toda la tabla; el unique index parcial de la sección 6 ya cubre el
  caso `status = 'active'` de forma indexada, este índice compuesto
  cubre además las consultas de historial que incluyen `revoked`/`draft`.
- `media_asset (artisan_id, piece_id, role, position)` para ordenar
  galería en una sola consulta.
- `audit_event.occurred_at` y `audit_event (entity_type, entity_id)`.

## 8. Campos obligatorios vs opcionales

- **Siempre obligatorios:** `id`, `created_at`, `updated_at` (excepto
  `audit_event`, sin `updated_at`); `slug` en `artisan`/`piece`;
  `artisan_id` en `piece`; `piece_id` en `certificate` (obligatorio en
  **cada fila** de `certificate`; ya no implica una sola fila por
  pieza — una pieza puede tener varias filas de `certificate` a lo
  largo del tiempo, sección 2.3, sección 4).
- **Opcionales por diseño:** todo campo narrativo/editorial
  (`biography`, `history`, `description`, `dimensions`, `origin`, etc.),
  porque el inventario actual tiene información parcial (`PROJECT.md`
  §11).
- **Opcionales por estado del flujo:** `nfc_tag.piece_id` (antes de
  asignar; obligatorio en la práctica cuando `status IN ('programmed',
  'locked')`), `nfc_tag.programmed_at` (antes de programar),
  `nfc_tag.locked_at` (solo cuando `status = locked`),
  `certificate.token_hash` (nulo solo mientras `status = draft`;
  obligatorio en `active`/`revoked`), `certificate.issued_at` (mientras
  está en `draft`), `certificate.revoked_at` (obligatorio cuando
  `status = revoked`, nulo en otro caso), `certificate.revocation_reason`
  (opcional siempre, incluso revocado).

## 9. Convenciones de estado de publicación

Aprobado:

```text
publication_status: draft | published | archived
```

Aplica a `artisan.publication_status` y `piece.publication_status`.
`certificate.status` y `nfc_tag.status` usan sus propios enums (secciones
2.3 y 2.4) porque representan ciclos de vida distintos, no visibilidad
editorial.

## 10. IDs internos vs IDs públicos

| Concepto | Dónde vive | Expuesto públicamente | Enumerable |
|---|---|---|---|
| `artisan.id` (UUID) | interno | No (se usa `slug`) | No |
| `artisan.slug` | público | Sí, en `/artesanos/{slug}` | Sí (intencional) |
| `piece.id` (UUID) | interno | No (se usa `slug`) | No |
| `piece.slug` | público | Sí, en `/piezas/{slug}` | Sí (intencional) |
| `piece.public_code` | público | Sí (referencia legible de la pieza) | Sí (no es secreto de seguridad) |
| `nfc_tag.physical_uid` | inventario | No en frontend público | Irrelevante — no es secreto (ADR-008) |
| `certificate.id` (UUID) | interno | No | No |
| certificado — token privado | nunca persistido en claro | Solo vía NFC físico | **No enumerable, CSPRNG (ADR-007)** |

Esta tabla formaliza que `piece.id`, `piece.public_code`,
`nfc_tag.physical_uid` y el token privado del certificado son conceptos
separados (ADR-008 extendido).

## 11. Timestamps

- Todas las entidades: `created_at`, `updated_at` (`timestamptz`, UTC),
  salvo `audit_event`.
- `certificate`: además `issued_at` (nulo mientras está en `draft`),
  `revoked_at` (nulo salvo cuando `status = revoked`, donde es
  obligatorio).
- `nfc_tag`: además `programmed_at`, `locked_at` (nullable).
- `audit_event`: solo `occurred_at`. Sin `updated_at`, porque el evento es
  append-only/inmutable (aprobado) y no se edita después de creado.

## 12. Campos sensibles a seguridad

- `certificate.token_hash`: dato sensible; acceso restringido a la capa
  de validación del backend. El token en claro **no** se modela como
  columna porque no debe existir en la base de datos en ningún momento
  posterior a la emisión. El algoritmo de hashing se define en
  `SECURITY.md`, no aquí.
- `nfc_tag.physical_uid`: no es secreto, pero no debe usarse como
  mecanismo de autenticación ni exponerse innecesariamente junto con
  datos de certificado (ADR-008).
- `artisan.languages`: público solo cuando `languages_public = true`;
  por defecto no se expone.
- `audit_event.ip_address`: dato personal; su retención y anonimización
  se definen en `SECURITY.md`.
- `audit_event.metadata` / `certificate.authenticity_metadata`: JSONB de
  forma libre; qué información no debe registrarse ahí se define en
  `SECURITY.md`.

## 13. Estado de las decisiones

Todos los puntos que en versiones anteriores de este documento estaban
marcados como `PROPOSED DECISION` fueron resueltos por Alexis (Product
Owner) el 2026-09-16, incluyendo los dos últimos pendientes:

- Unidades permitidas en `dimensions.unit`: `mm | cm | in` (sección 2.2).
- Comportamiento de borrado en FKs para `certificate.piece_id`,
  `nfc_tag.piece_id` y `media_asset.artisan_id`/`piece_id` (sección 5).

## 14. Reemisión de certificado / historial (resuelve `CROSS-DOCUMENT CHANGE REQUIRED` de `SECURITY.md`)

Esta revisión (2026-09-16, rama `docs/certificate-history`) resuelve el
único `CROSS-DOCUMENT CHANGE REQUIRED` pendiente identificado en
`SECURITY.md` §14.1 y §20: `certificate.piece_id` deja de ser unique de
forma global y pasa a tener un unique index **parcial** sobre
`piece_id` donde `status = 'active'` (restricción C', sección 4;
detalle conceptual en sección 2.3; unique constraints actualizados en
sección 6; índice compuesto adicional en sección 7).

Resumen de lo que cambia:

- Una pieza puede tener **múltiples registros históricos** de
  `CERTIFICATE` (secciones 2.3, 4).
- Como máximo **uno** de esos registros puede tener `status = 'active'`
  por pieza, en cualquier momento (secciones 2.3, 4, 6).
- Los certificados `revoked` **permanecen preservados**, nunca se
  borran ni se sobrescriben (sección 2.3).
- Un certificado de reemplazo recibe un `token_hash` derivado de un
  **token completamente nuevo e independiente** (sección 2.3, ADR-007).
- Un `token_hash` de una fila `revoked` **nunca** vuelve a producir
  `authentic`, porque la verificación exige `status = 'active'`
  (sección 2.3, ya consistente con `SECURITY.md` §3.2/§14).
- `certificate.token_hash` **sigue siendo unique global**, sin cambios
  (sección 6).

Esta revisión **no modifica** el comportamiento observable de la API:
`API_CONTRACT.md` no se toca en esta tarea, y la resolución pública de
certificado sigue teniendo únicamente los estados `authentic` /
`unavailable` (`API_CONTRACT.md` §7, sin cambios).

**Enlace explícito de reemplazo — fuera de alcance del MVP (aprobado
por Alexis el 2026-09-16, ver también sección 2.3):** este documento no
agrega una columna que enlace explícitamente un certificado de
reemplazo con el certificado `revoked` al que sucede (ej.
`replaces_certificate_id`). El historial de certificados ya queda
preservado por permitir múltiples filas por pieza, y `issued_at` /
`revoked_at` / `status` bastan como historial de ciclo de vida para el
piloto actual — no se sobre-diseña una cadena certificado-a-certificado
para 2 artesanos / 4 piezas. Si en el futuro surge una necesidad real de
producto o auditoría, una relación auto-referenciada puede agregarse
mediante una migración aditiva, sin alterar lo definido en esta
revisión.

## 15. Estado de las decisiones (actualizado)

Todos los puntos que en versiones anteriores de este documento estaban
marcados como `PROPOSED DECISION` fueron resueltos por Alexis (Product
Owner) el 2026-09-16, incluyendo los dos últimos pendientes de esa
revisión:

- Unidades permitidas en `dimensions.unit`: `mm | cm | in` (sección 2.2).
- Comportamiento de borrado en FKs para `certificate.piece_id`,
  `nfc_tag.piece_id` y `media_asset.artisan_id`/`piece_id` (sección 5).

Esta revisión posterior (reemisión de certificado / historial, sección
14) resuelve el `CROSS-DOCUMENT CHANGE REQUIRED` de `SECURITY.md`
descrito arriba. El único punto que había quedado marcado como
`PROPOSED DECISION` en esa revisión (columna explícita de trazabilidad
certificado-reemplazo → certificado-reemplazado, ej.
`replaces_certificate_id`) fue resuelto por Alexis (Product Owner) el
2026-09-16: **no se agrega en el MVP** (sección 2.3, sección 14) — el
historial ya preservado vía múltiples filas de `CERTIFICATE` por pieza,
junto con `issued_at`/`revoked_at`/`status`, es suficiente para el
piloto actual; una relación auto-referenciada queda como extensión
aditiva futura si una necesidad real de producto/auditoría lo justifica.

**No quedan `PROPOSED DECISION` abiertos en este documento.** Cualquier
otro punto no cubierto aquí pertenece explícitamente a otro documento
(ver sección 0: `SECURITY.md`, `API_CONTRACT.md`, migración de datos
legado) o a un ADR futuro (ownership, sección 3).
