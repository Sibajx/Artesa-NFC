# ArtesaNFC — Contrato de API

**Estado:** Aprobado para MVP
**Issue:** #4 — docs: create API_CONTRACT.md
**Dominio de referencia:** `api.artesanfc.com`
**Fecha de referencia:** 2026-09-16

## 0. Propósito y alcance

Este documento define el **contrato de interfaz** entre el frontend
(Cloudflare Pages) y el backend (FastAPI), consumido por ambos equipos. No
implementa la API ni redefine el modelo de datos: es la traducción del
modelo ya aprobado en `docs/DATA_MODEL.md` hacia la superficie pública
JSON, siguiendo `docs/ARCHITECTURE.md` §7 y `docs/PROJECT.md` §7-8.

Fuera de alcance de este documento (pertenecen a otros documentos):

- **`docs/DATA_MODEL.md`**: no se rediseña ni se reabren sus decisiones
  aprobadas. Este documento solo decide cómo se **expone** ese modelo. No
  se modifica `DATA_MODEL.md` como parte de esta revisión.
- **`docs/SECURITY.md`**: algoritmo exacto de hashing del token,
  implementación detallada de rate limiting, autenticación administrativa
  detallada, políticas de logging/retención. Aquí solo se documenta el
  comportamiento observable desde la interfaz (qué no se debe filtrar).
- **`docs/DESIGN_SYSTEM.md`**: presentación visual; este documento solo
  define la forma de los datos que el frontend consume.

## 1. Namespace y prefijo

```text
/api/v1
```

Todas las rutas de este documento cuelgan de este prefijo, según
`ARCHITECTURE.md` §7.

## 2. Convención de nombres / serialización

**Aprobado:** la API pública usa **`snake_case`** de forma consistente,
tanto en el modelo interno (`DATA_MODEL.md`) como en los cuerpos JSON de
request y response de este contrato. No existe una capa de traducción a
`camelCase`.

Los nombres de campo público no son necesariamente idénticos a los
nombres de columna interna (ver secciones 4-6): se documentan
explícitamente los que difieren o se omiten.

## 3. Endpoints públicos (mínimo para el MVP)

```text
GET  /api/v1/artisans
GET  /api/v1/artisans/{slug}

GET  /api/v1/pieces
GET  /api/v1/pieces/{slug}

POST /api/v1/certificates/resolve
```

Regla explícita (ADR-006, `PROJECT.md` §8): **no existen** endpoints de
listado o lectura directa de certificados por id para navegación pública.
En particular, no se definen:

```text
GET /api/v1/certificates
GET /api/v1/certificates/{id}
```

La única forma de resolver un certificado es `POST
/api/v1/certificates/resolve` con el token privado obtenido físicamente
del NFC (sección 7).

## 4. Representación pública de ARTISAN

```json
{
  "slug": "artesano-de-cuilapam",
  "full_name": "Nombre completo",
  "artistic_name": null,
  "biography": "Reseña breve...",
  "history": null,
  "location": {
    "locality": "Cuilápam de Guerrero",
    "municipality": "Cuilápam de Guerrero",
    "state": "Oaxaca",
    "country": "México"
  },
  "techniques": ["tallado en madera", "pintura natural"],
  "languages": [],
  "public_contact": null,
  "media": [],
  "pieces": [
    {
      "slug": "mascara-01",
      "name": "Máscara ceremonial",
      "public_code": "PIEZA-0001",
      "availability_status": "available",
      "cover_media": null
    }
  ]
}
```

Reglas de mapeo desde `DATA_MODEL.md` §2.1:

- `slug`, `full_name`, `artistic_name`, `biography`, `history`,
  `public_contact` se exponen tal cual; si el valor interno es nulo o no
  existe, el campo se serializa como `null` (nunca se omite la clave —
  ver sección 12).
- `location` siempre está presente como objeto con las cuatro claves
  `locality`/`municipality`/`state`/`country`; cualquiera de ellas es
  `null` si el dato interno no existe. `location` en sí nunca es
  omitido, aunque las cuatro claves internas estén vacías (en ese caso
  se envía `location` con las cuatro en `null`).
- `techniques` se serializa siempre como array de strings (`[]` si no
  hay datos), sin importar si internamente es `text[]` o `jsonb`.
- `languages` **siempre está presente como array** (nunca se omite ni es
  `null`). Contiene los valores reales solo si `languages_public =
  true`; en cualquier otro caso (no autorizado, o simplemente sin
  datos) se serializa como `[]`. Esto es deliberado: un observador
  externo no puede distinguir "el artesano no declaró lenguas" de "el
  artesano declaró lenguas pero no autorizó mostrarlas", porque ambos
  casos producen el mismo `[]`.
- `pieces` es un resumen (no la representación completa de PIECE) para
  evitar payloads pesados en el perfil del artesano; solo incluye lo
  necesario para enlazar y previsualizar (ADR-003: relación
  bidireccional). Es `[]` si el artesano no tiene piezas publicadas.
- `cover_media` dentro de cada resumen de `pieces` es un objeto
  `MEDIA_ASSET` público (sección 6) con `role = hero`, o `null` si la
  pieza no tiene uno.

Campos que **no** se exponen (ver también sección 11):

- `id` (UUID interno) — el frontend navega por `slug`.
- `publication_status` — el filtrado de publicación se resuelve en el
  backend (sección 9); un artesano no publicado simplemente no aparece.
- `created_at` / `updated_at` — sin uso conocido en frontend público. Si
  en el futuro se necesita, se agrega explícitamente en una revisión de
  este documento (adición no disruptiva, sección 13).

## 5. Representación pública de PIECE

```json
{
  "slug": "mascara-01",
  "public_code": "PIEZA-0001",
  "name": "Máscara ceremonial",
  "description": "Descripción corta...",
  "history": null,
  "materials": ["madera de copal", "pintura natural"],
  "technique": "Tallado y pintura tradicional",
  "origin": "Cuilápam de Guerrero, Oaxaca",
  "creation_year": 2024,
  "creation_date": null,
  "dimensions": { "height": 30, "width": 20, "depth": 15, "unit": "cm" },
  "visual_theme": null,
  "availability_status": "available",
  "artisan": {
    "slug": "artesano-de-cuilapam",
    "full_name": "Nombre completo",
    "artistic_name": null
  },
  "media": []
}
```

Reglas de mapeo desde `DATA_MODEL.md` §2.2:

- Mapeo directo de `slug`, `public_code`, `name`, `description`,
  `history`, `technique`, `origin`, `creation_year`, `creation_date`,
  `dimensions`, `visual_theme`, `availability_status`. Cualquier campo
  opcional sin valor se serializa como `null` (sección 12); ninguno se
  omite.
- `materials` se serializa siempre como array de strings, `[]` si no hay
  datos (mismo criterio que `techniques` en ARTISAN).
- `artisan` es un resumen embebido, siempre presente (no requiere una
  segunda llamada para mostrar el enlace bidireccional pieza →
  artesano, ADR-003).
- `media` es siempre un array, `[]` si la pieza no tiene medios
  publicados.

**Regla de no divulgación de certificado/NFC (aprobada):** la
representación pública de PIECE, y por lo tanto `GET
/api/v1/pieces/{slug}` y `GET /api/v1/pieces`, **no revela si la pieza
tiene certificado o tag NFC asociado**. La experiencia pública de la
pieza y la experiencia privada del certificado permanecen completamente
separadas (`PROJECT.md` §2.3, ADR-005, ADR-006). En consecuencia, este
contrato **prohíbe explícitamente** los siguientes campos en cualquier
respuesta pública de PIECE, en cualquier revisión futura no disruptiva:

- `has_certificate`
- `certificate_id`
- `certificate_status`
- `has_nfc`
- cualquier campo o subobjeto de `nfc_tag` (estado, UID, fechas de
  programación/bloqueo)

La resolución de certificado comienza **únicamente** desde el flujo del
token privado (sección 7); no existe ningún indicio, directo o
indirecto, de la existencia de un certificado en la superficie pública
de la pieza.

Campos que **no** se exponen (además de lo anterior):

- `id` (UUID interno) — se usa `slug`.
- `artisan_id` (UUID interno) — se reemplaza por el objeto `artisan`
  embebido.
- `publication_status` — filtrado en backend (sección 9).

## 6. Representación de MEDIA_ASSET (reutilizable)

```json
{
  "type": "image",
  "role": "hero",
  "url": "https://cdn.artesanfc.com/....jpg",
  "alt_text": "Máscara ceremonial de madera pintada a mano",
  "position": 0,
  "format": {
    "width": 1920,
    "height": 1080,
    "mime_type": "image/jpeg"
  }
}
```

- `type`: uno de `image`, `video`, `model_3d`, `sequence_360` (idéntico
  a `media_type` en `DATA_MODEL.md` §2.5, renombrado a `type` por
  brevedad en el contrato público).
- `role`: mismo catálogo aprobado en `DATA_MODEL.md` §2.5 (`hero`,
  `gallery`, `detail`, `process`, `portrait`, `document`, `model_3d`,
  `sequence_360`).
- `url`: URL pública final del recurso. **No se expone `storage_path`
  interno** ni ningún detalle de infraestructura (ver lista abajo).
- `alt_text`: obligatorio (no `null`) si `type = image`; `null` para
  otros tipos si no aplica.
- `position`: entero, igual que en el modelo interno, para que el
  frontend ordene sin lógica adicional.
- `format`: objeto **con esquema fijo y seguro por tipo** (aprobado),
  ver tabla abajo. Cualquier clave no listada en la tabla no se expone.

### Esquema de `format` por tipo (aprobado)

| `type` | Claves de `format` |
|---|---|
| `image` | `width` (integer), `height` (integer), `mime_type` (string) |
| `video` | `width` (integer), `height` (integer), `duration_seconds` (number), `mime_type` (string) |
| `model_3d` | `format` (string, ej. `"glb"`), `file_size_bytes` (integer) |
| `sequence_360` | `frame_count` (integer), `width` (integer), `height` (integer) |

Cualquier clave dentro de `format` cuyo valor interno no se conozca se
serializa como `null`, no se omite (sección 12). Esta tabla es
extensible en revisiones futuras **no disruptivas** (agregar una clave
opcional nueva a un tipo existente, o un `type` nuevo con su propio
esquema), siguiendo el criterio de versionado de la sección 13.

Explícitamente **no** se exponen dentro de `format` ni en ningún otro
campo de `MEDIA_ASSET`:

- rutas de almacenamiento internas (`storage_path`);
- nombres de bucket;
- rutas de sistema de archivos del servidor;
- identificadores de objeto privados (ej. claves de storage, IDs de
  proveedor externo);
- cualquier otro metadato de infraestructura.

Campos que **no** se exponen: `id` (UUID interno de media),
`artisan_id`/`piece_id` (el objeto ya viene anidado dentro de
artisan/piece, es redundante y expondría UUIDs internos), `status`
(`active`/`archived` — un asset archivado simplemente no se incluye en
la respuesta).

## 7. Resolución de certificado

```text
POST /api/v1/certificates/resolve
```

### Request

```json
{
  "token": "cadena-opaca-recibida-del-nfc"
}
```

- `token`: el valor recibido en la URL privada del certificado (ADR-007,
  ADR-008). El cliente nunca envía `piece_id`, `certificate.id` ni
  ningún otro identificador — solo el token.

### Response — éxito (certificado activo y válido)

```json
{
  "authenticity": {
    "status": "authentic",
    "certificate_version": 1,
    "issued_at": "2026-01-10T00:00:00Z"
  },
  "piece": { /* representación pública de PIECE, sección 5 */ },
  "artisan": { /* representación pública de ARTISAN, sección 4 */ },
  "authenticity_metadata": {
    "notes": null
  }
}
```

### Response — certificado no resoluble (aprobado: único estado público genérico)

```json
{
  "authenticity": {
    "status": "unavailable"
  }
}
```

**Aprobado:** existe un **único** resultado público para cualquier
token/certificado que no produzca una autenticidad válida:
`authenticity.status = "unavailable"`. Este estado cubre, sin
distinción a nivel de contrato público:

- token inexistente;
- certificado revocado;
- token malformado/inválido;
- cualquier otro caso en que el certificado no sea utilizable.

Esta respuesta se mantiene **mínima**: no incluye `piece`, `artisan` ni
`authenticity_metadata`, sin importar cuál de los casos anteriores la
originó. El backend puede (y debe, para auditoría/seguridad) distinguir
estos casos **internamente** (ej. en `AUDIT_EVENT`), pero la API
pública nunca expone esa distinción.

Este documento fija únicamente la forma pública (`unavailable` como
resultado genérico); la implementación detallada de anti-enumeración —
indistinguibilidad en tiempo de respuesta, cabeceras, y cualquier otro
detalle a nivel de transporte necesario para que `authentic` y
`unavailable` sean indistinguibles hasta el punto en que la seguridad lo
requiera — permanece delegada a `SECURITY.md` y no se congela aquí.

### Qué nunca aparece en ninguna respuesta de este endpoint

- `token_hash` (ni completo ni parcial).
- `nfc_tag.physical_uid` o cualquier dato de `NFC_TAG`.
- `certificate.id` (UUID interno).
- `piece.id` / `artisan.id` (UUID interno) — se usan los mismos objetos
  públicos de las secciones 4 y 5, que ya excluyen el UUID.
- Cualquier dato de `AUDIT_EVENT`.
- Detalles de por qué falló la resolución (inexistente, revocado y
  malformado se colapsan siempre en el único resultado público
  `unavailable`, sin distinción).

### `authenticity_metadata`: proyección explícita (allowlist)

**Aprobado:** `certificate.authenticity_metadata` (JSONB libre y
versionable en `DATA_MODEL.md` §2.3) **nunca se expone completo**. La
API expone únicamente una proyección explícita (allowlist) de claves
consideradas seguras para presentación pública de autenticidad.
Cualquier clave almacenada internamente que no esté en esta lista
permanece interna y nunca se serializa.

Allowlist inicial (mínima):

| Clave pública | Tipo | Descripción |
|---|---|---|
| `notes` | string \| `null` | Nota breve, curada por administración, sobre el contexto de la certificación (ej. "certificado en el marco del caso piloto de Cuilápam"). `null` si no hay nota. |

No se define ninguna otra clave pública en esta revisión. Ampliar esta
allowlist con una clave nueva es un cambio **no disruptivo** (sección
13) y se documenta actualizando esta tabla, no inventando contenido de
producto por adelantado.

### Fuera de alcance de este endpoint (pertenece a `SECURITY.md`)

- Algoritmo de verificación del token contra `token_hash`.
- Rate limiting (límite de intentos, ventana, bloqueo por IP/token).
- Logging de intentos fallidos hacia `AUDIT_EVENT`.
- Semántica exacta de indistinguibilidad anti-enumeración a nivel de
  transporte/tiempo de respuesta (ver nota arriba).

## 8. Listados, filtrado y paginación

Inventario actual: 2 artesanos, 4 piezas (`PROJECT.md` §11). **Aprobado
para el MVP:** no se implementa paginación real; se define una
envoltura simple y forward-compatible.

### `GET /api/v1/artisans`

- Sin filtros en el MVP más allá de la publicación (resuelta
  internamente, sección 9).
- Respuesta: la envoltura `data`/`meta` de la sección "Envoltura de
  listado" abajo, donde `data` contiene un array de **resúmenes de
  artesano** (mismos campos que el resumen embebido en
  `piece.artisan`, sección 5), **no** el objeto completo de la sección 4
  (evita cargar `pieces`/`media` completos de todos los artesanos en un
  solo listado).

### `GET /api/v1/pieces`

Filtros opcionales vía query string:

```text
GET /api/v1/pieces?artisan=artesano-de-cuilapam
GET /api/v1/pieces?availability_status=available
```

- `artisan`: filtra por `artisan.slug`.
- `availability_status`: filtra por el enum público de `DATA_MODEL.md`
  §2.2 (`available`, `reserved`, `exhibited`, `archived`).
- `publication_status` **no** es un filtro público: se resuelve siempre
  en el backend (sección 9), nunca como parámetro de query expuesto.
- Respuesta: la misma envoltura `data`/`meta` abajo, donde `data`
  contiene un array de **resúmenes de pieza** (mismos campos que el
  resumen embebido en `artisan.pieces`, sección 4).

### Envoltura de listado (aprobada)

El cuerpo de respuesta de nivel superior de `GET /api/v1/artisans` y
`GET /api/v1/pieces` es **siempre** esta envoltura, nunca un array en la
raíz de la respuesta:

```json
{
  "data": [ /* artisans o pieces */ ],
  "meta": {
    "total": 4
  }
}
```

- En el MVP, `data` contiene siempre el conjunto completo (sin límite
  artificial), y `meta.total` simplemente refleja `data.length`.
- No se implementa paginación real todavía; `meta` se reserva para
  cuando el catálogo lo justifique.

### Dirección futura preferida (documentada, no congelada)

Si el catálogo crece lo suficiente para requerir paginación, la
dirección preferida es **paginación por cursor** (`meta.next_cursor` +
parámetro de query `cursor`), por ser estable ante inserciones
concurrentes a diferencia de offset/limit. Esto es una **preferencia
documentada, no un contrato congelado**: el diseño exacto del cursor
(codificación, campos de orden, tamaño de página por defecto) se
definirá en una revisión futura de este documento cuando exista una
necesidad real, como adición no disruptiva (sección 13).

## 9. Publicación y visibilidad (regla transversal)

**Invariante de publicación (aprobado):** una pieza es públicamente
visible **si y solo si**:

```text
piece.publication_status = published
Y
piece.artisan.publication_status = published
```

Si una pieza está `published` pero su artesano no lo está, los
endpoints públicos deben tratar esa pieza como **no pública**: no
aparece en `GET /api/v1/pieces`, no aparece en el listado embebido de
`artisan.pieces` (que ya no aplicaría, dado que el propio artesano no es
público), y `GET /api/v1/pieces/{slug}` responde como si no existiera
(sección 10, `404` genérico).

Este es un estado inconsistente que el flujo administrativo debería
prevenir en el origen (ej. no permitir publicar una pieza cuyo artesano
no está publicado); esa validación administrativa es responsabilidad de
una futura revisión de la API administrativa (sección 14) y del
backend, no de este documento. Este contrato solo fija el
**comportamiento observable** si esa inconsistencia llegara a ocurrir.
No se modifica `DATA_MODEL.md` para reflejar esta regla — es una regla
de exposición de API, no una restricción de base de datos.

Reglas adicionales:

- Todos los endpoints públicos de este documento **solo devuelven
  registros con `publication_status = published`** (`DATA_MODEL.md`
  §9). `draft` y `archived` nunca aparecen en respuestas públicas, sin
  excepción y sin parámetro para forzarlo.
- `availability_status` es independiente de `publication_status`
  (`DATA_MODEL.md` §2.2): una pieza `archived` en disponibilidad puede
  seguir `published` editorialmente (ej. pieza histórica que ya no está
  disponible pero sigue siendo parte del catálogo mostrado).

## 10. Comportamiento HTTP

### Content-Type

- Request y response: `application/json; charset=utf-8` en todos los
  endpoints de este documento.

### Códigos de estado

| Código | Cuándo |
|---|---|
| `200 OK` | Lectura o resolución exitosa (incluye `certificates/resolve` con `status: authentic` o `status: unavailable` — ver nota abajo). |
| `400 Bad Request` | Solicitud semánticamente inválida que **no** es un error ordinario de validación de esquema/entrada (ej. una combinación de parámetros de query mutuamente excluyentes, o una regla de negocio de la solicitud que no puede expresarse como validación de esquema). |
| `404 Not Found` | `GET /api/v1/artisans/{slug}` o `GET /api/v1/pieces/{slug}` cuando el slug no corresponde a ningún recurso publicado (incluye el caso del invariante de la sección 9). |
| `405 Method Not Allowed` | Método HTTP no soportado en una ruta existente (ej. `DELETE /api/v1/pieces`). |
| `422 Unprocessable Entity` | Errores de validación de entrada de la solicitud — body, parámetros de query y parámetros de path — incluyendo tipo incorrecto, campo faltante, o valor fuera del rango/enum esperado (ej. `token` ausente en `certificates/resolve`, o `availability_status=xyz` en un filtro de query). Corresponde al comportamiento estándar de validación de FastAPI/Pydantic; no se requiere convertir estos casos a `400`. |
| `429 Too Many Requests` | Rate limiting activado (mecanismo definido en `SECURITY.md`; el código y la forma de respuesta sí son parte de este contrato). |
| `500 Internal Server Error` | Error no controlado del servidor. |

**Nota sobre `certificates/resolve`:** este endpoint responde `200 OK`
tanto para `authentic` como para `unavailable`, porque la resolución en
sí fue exitosa (el servidor procesó la solicitud correctamente); el
resultado de la resolución viaja en el cuerpo (`authenticity.status`).
Esto evita que el código HTTP filtre información más granular que el
propio cuerpo de respuesta ya decidió no filtrar (sección 7). `422` en
este endpoint se reserva solo para errores de validación de la
solicitud en sí (ej. `token` ausente o de tipo incorrecto), nunca para
distinguir por qué un token no produjo un certificado válido — ese
resultado siempre es `200 OK` con `status: unavailable`.

### Forma de error (para `400`, `404`, `405`, `422`, `429`, `500`)

```json
{
  "error": {
    "code": "not_found",
    "message": "The requested resource does not exist."
  }
}
```

- `code`: string estable en `snake_case`, pensado para lógica del
  cliente (ej. `validation_error`, `not_found`, `method_not_allowed`,
  `rate_limited`, `internal_error`).
- `message`: texto legible, no sensible, sin detalles de
  implementación.
- **Nunca** se incluyen stack traces, mensajes de excepción de
  base de datos, nombres de tabla/columna, ni rutas de archivo del
  servidor.

### `error.details` en respuestas `422` (aprobado)

`error.details` es opcional y, cuando está presente, contiene
**únicamente información segura de validación de entrada del cliente**:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request body is invalid.",
    "details": [
      { "field": "token", "reason": "This field is required." }
    ]
  }
}
```

- `details` es un array de objetos `{ "field": string, "reason": string
  }`.
- `field`: nombre del campo de entrada tal como lo conoce el cliente
  (el mismo nombre `snake_case` del contrato público, no un nombre de
  columna interna).
- `reason`: texto humano-legible sobre por qué la entrada es inválida
  (ej. "This field is required.", "Must be a string.").

`error.details` **nunca** incluye:

- SQL o fragmentos de consultas;
- nombres de tabla o columna de base de datos;
- nombres de excepciones internas o clases del backend;
- stack traces;
- hashes (ej. `token_hash`) o cualquier valor derivado de ellos;
- IDs internos (UUID) de ninguna entidad;
- cualquier otro detalle de implementación del backend.

## 11. Público vs. interno vs. sensible a seguridad

| Categoría | Ejemplos | Regla |
|---|---|---|
| **Público-seguro** | `slug`, `full_name`, `biography`, `techniques`, `public_contact`, `name`, `description`, `dimensions`, `availability_status`, `media.url`/`alt_text`/`role`/`format` | Se expone sin restricciones adicionales, sujeto a `publication_status = published` (sección 9). |
| **Público condicional** | `artisan.languages` | Contenido real solo si `languages_public = true`; en otro caso `[]` (sección 4). |
| **Público con proyección explícita (allowlist)** | `certificate.authenticity_metadata` | Solo las claves listadas en la allowlist de la sección 7 (`notes`); el resto del JSONB interno nunca se expone. |
| **Interno, no expuesto salvo necesidad técnica** | `artisan.id`, `piece.id`, `certificate.id`, `media_asset.id`, `nfc_tag.id` (todos UUID) | No se exponen en el MVP porque `slug`/`public_code` cubren toda referencia pública necesaria. Si en el futuro el frontend necesita un UUID (ej. para una mutación administrativa), se documenta explícitamente en ese momento, no preventivamente. |
| **Nunca expuesto en la superficie pública de PIECE** | `has_certificate`, `certificate_id`, `certificate_status`, `has_nfc`, cualquier campo de `nfc_tag` | Prohibido explícitamente (sección 5), incluso como indicador booleano, para no revelar la existencia de un certificado desde la navegación pública. |
| **Absolutamente prohibido, en cualquier endpoint presente o futuro** | `certificate.token_hash`, el token privado del certificado en texto plano | Nunca se serializa en ninguna respuesta de ningún endpoint, público o administrativo, exista hoy o se agregue en el futuro. Esta es la única categoría de este documento que también restringe explícitamente a la futura API administrativa. |
| **No expuesto por los endpoints públicos de este documento** | `nfc_tag.physical_uid`, UUIDs internos (`artisan.id`, `piece.id`, `certificate.id`, `media_asset.id`, `nfc_tag.id`), `audit_event.*`, `actor_id`/`actor_type`, rutas de almacenamiento/bucket internas | Ninguno de los endpoints públicos definidos en las secciones 3-9 de este documento expone estos campos. Este documento **no** decide si o cómo una futura API administrativa los expondría: cualquier exposición operativa/interna a través de `/api/v1/admin/...` es una decisión separada, que debe documentarse explícitamente en el futuro contrato administrativo (sección 14) y protegerse con la autorización que defina `SECURITY.md`. No se asume aquí ni prohibición ni permiso para el admin — solo se fija que hoy no existe ningún endpoint (público o administrativo) que los exponga. |
| **Filtrado por publicación** | `artisan`/`piece` con `publication_status != published`, o una pieza `published` cuyo artesano no lo está (sección 9) | Nunca aparecen en endpoints públicos; no existe parámetro para forzar su inclusión desde fuera de la API administrativa. |

Esta tabla es la referencia única para auditar cualquier endpoint nuevo
que se agregue a este documento en el futuro: si un campo no aparece
aquí como público (en alguna de sus tres formas), no se expone sin antes
actualizar esta sección. La única regla de esta tabla que se extiende
por diseño a un futuro contrato administrativo no escrito todavía es la
prohibición absoluta de `token_hash`/token en texto plano.

## 12. Convención sobre valores ausentes (aprobada)

Convención global de serialización, aplicable a todos los endpoints de
este documento:

- **Campo escalar opcional sin valor → `null`.** Nunca se omite la
  clave (ej. `artistic_name`, `history`, `visual_theme` cuando faltan).
- **Objeto opcional sin valor → `null`.** Ej. `public_contact`,
  `cover_media`, `dimensions` cuando no existen.
- **Lista sin elementos → `[]`.** Nunca `null` para una lista (ej.
  `techniques`, `materials`, `media`, `languages`, `pieces`).
- Los campos documentados en este contrato son **estables**: no
  desaparecen de forma impredecible según los datos disponibles. Un
  cliente puede asumir que toda clave descrita en las secciones 4-7
  siempre está presente en la respuesta, con `null`/`[]` como valor
  cuando no hay dato.

Esta convención reemplaza el criterio anterior de "omitir campos vacíos"
y aplica retroactivamente a todos los ejemplos de este documento
(secciones 4-7). La única excepción intencional es `languages`
(sección 4), donde `[]` cumple una doble función: "sin datos" y "datos
no autorizados para mostrar" son indistinguibles a propósito.

## 13. Versionado

```text
/api/v1
```

**Principio aprobado:** `/api/v1` no debe recibir cambios disruptivos de
forma silenciosa. Un cambio disruptivo requiere `/api/v2`; un cambio
aditivo/no disruptivo puede permanecer dentro de `/api/v1`.

Se considera **cambio no disruptivo** (permanece en `/api/v1`):

- Agregar un campo nuevo, opcional, a una respuesta existente (con
  `null`/`[]` como valor por defecto, sección 12).
- Agregar un endpoint nuevo.
- Agregar un valor nuevo a un enum ya usado en un campo, siempre que el
  frontend actual lo ignore de forma segura (ej. un nuevo `role` de
  media, o una clave nueva en la allowlist de `authenticity_metadata`).
- Agregar un `type` nuevo de `MEDIA_ASSET` con su propio esquema de
  `format` (sección 6).
- Relajar una validación de request (aceptar algo que antes era
  rechazado).
- Definir el diseño concreto de paginación por cursor cuando se
  necesite (sección 8), siempre que `data`/`meta` se mantengan como
  envoltura.

Se considera **cambio disruptivo** (requiere `/api/v2`):

- Eliminar o renombrar un campo de una respuesta existente.
- Cambiar el tipo de un campo existente (ej. `creation_year` de número a
  string), o cambiar la convención de la sección 12 (ej. volver a omitir
  campos en vez de `null`/`[]`).
- Cambiar el significado de un `status`/enum ya existente.
- Cambiar la forma del error estándar (sección 10).
- Cambiar el comportamiento de `certificates/resolve` de forma que
  reemplace el único estado público `unavailable` por múltiples estados
  distinguibles (ej. volver a separar `revoked`/`not_found`), o que
  reintroduzca `piece`/`artisan` en una respuesta que no sea
  `authentic`. Esta distinción es explícitamente una decisión de
  seguridad, no de contrato, y no se congela en `/api/v1` en ninguna
  dirección: ni afinar `unavailable` en varios estados públicos, ni
  ningún otro cambio a esta semántica, sin coordinación con
  `SECURITY.md` y sin pasar por `/api/v2` si el cambio es disruptivo
  para el frontend ya integrado.
- Exponer cualquier campo listado como prohibido en la sección 5 u 11
  (ej. `has_certificate`).
- Cualquier renombrado de endpoint o de propiedad JSON compartida, por
  la regla 4 de `COLLABORATION_PROMPT.md` — requiere documentarse antes
  de implementarse, no solo versionarse después.

**Aprobado:** no se define todavía un período fijo de deprecación entre
`/api/v1` y un eventual `/api/v2`. Una política concreta de deprecación
(cuánto tiempo coexisten ambas versiones, cómo se comunica) solo se
definirá cuando exista efectivamente una `/api/v2`, no de forma
especulativa ahora.

## 14. API administrativa (solo espacio de nombres conceptual)

`ARCHITECTURE.md` §7 anticipa un namespace administrativo:

```text
/api/v1/admin/...
```

**Aprobado:** este namespace permanece **puramente conceptual**. Este
documento no congela un contrato CRUD administrativo completo, porque:

- La autenticación administrativa detallada no está definida
  (`ARCHITECTURE.md` §16: backend depende de `SECURITY.md`, que aún no
  existe).
- `DATA_MODEL.md` §2.6 deja explícitamente fuera del MVP el modelo de
  usuario administrativo (`actor_type`/`actor_id` mínimo, sin más).

Lo único que se documenta como orientación, sin ser un contrato
definitivo:

```text
POST   /api/v1/admin/artisans
PATCH  /api/v1/admin/artisans/{id}
POST   /api/v1/admin/pieces
PATCH  /api/v1/admin/pieces/{id}
POST   /api/v1/admin/certificates
POST   /api/v1/admin/certificates/{id}/revoke
```

(Idéntico al listado conceptual ya existente en `ARCHITECTURE.md` §7;
no se agrega ningún endpoint nuevo aquí.)

Explícitamente **no congelado en este documento** — trabajo futuro que
requiere coordinación con `SECURITY.md` y diseño de backend/admin:

- Mecanismo de autenticación (sesión, JWT, API key) y su transporte.
- Cuerpos de request/response completos de cada endpoint administrativo
  (qué campos son editables, validaciones, forma de error específica).
- Estrategia de identificador administrativo (`id` UUID vs. `slug` en la
  URL).
- Paginación y filtrado administrativo (puede diferir del público, ej.
  sí necesitar ver `draft`/`archived`).
- Roles/niveles de autorización administrativa.
- Mapeo detallado entre cada endpoint administrativo y
  `audit_event.action`.

Este documento se actualizará con el contrato administrativo completo
cuando exista `SECURITY.md` y una decisión de autenticación aprobada, no
antes.

## 15. Estado de las decisiones

Todos los puntos que en la versión anterior de este documento estaban
marcados como `PROPOSED DECISION` fueron resueltos por Alexis (Product
Owner) el 2026-09-16:

1. Convención de nombres `snake_case` en JSON público — aprobada
   (sección 2).
2. No divulgación de certificado/NFC en la representación pública de
   pieza — aprobada y prohibida explícitamente (sección 5).
3. Esquema de metadata de formato por tipo de medio — aprobado y
   definido (sección 6).
4. Único estado público genérico `unavailable` para cualquier
   token/certificado no resoluble (inexistente, revocado, malformado u
   otro), en vez de distinguir `revoked`/`not_found` — aprobado; la
   distinción interna para auditoría y la semántica exacta de
   anti-enumeración quedan coordinadas con `SECURITY.md` (sección 7).
5. Proyección explícita (allowlist) de `authenticity_metadata` —
   aprobada, con `notes` como única clave inicial (sección 7).
6. No implementar paginación real en el MVP, mantener `data`/`meta`
   forward-compatible, documentar cursor como dirección preferida sin
   congelarlo — aprobado (sección 8).
7. Invariante de publicación pieza+artesano — aprobado (sección 9); la
   prevención de la inconsistencia en origen queda como trabajo de
   validación administrativa futura, sin cambios a `DATA_MODEL.md`.
8. Forma de `error.details` en `422`, limitada a información segura de
   validación de entrada — aprobada (sección 10).
9. Convención global de `null`/`[]` en vez de omitir campos — aprobada
   (sección 12).
10. Principio de versionado sin período fijo de deprecación — aprobado
    (sección 13).
11. API administrativa como namespace puramente conceptual, sin CRUD
    congelado — aprobado (sección 14).

Correcciones de consistencia adicionales aplicadas en esta revisión
(2026-09-16), no `PROPOSED DECISION` sino ajustes de redacción/alcance:

12. La restricción "nunca expuesto, público o administrativo" se acotó:
    solo `token_hash`/token en texto plano son absolutamente prohibidos
    incluso para un futuro admin; el resto de campos sensibles
    (`nfc_tag.physical_uid`, UUIDs internos, `audit_event.*`) están
    fuera de los endpoints **públicos** de este documento, sin prejuzgar
    una futura API administrativa (sección 11).
13. `400`/`422` simplificados para alinearse con el comportamiento
    estándar de FastAPI/Pydantic: `422` cubre toda validación de
    entrada (body, query, path); `400` queda reservado para solicitudes
    semánticamente inválidas que no son validación ordinaria de
    esquema (sección 10).
14. Envoltura `data`/`meta` aclarada como el cuerpo de nivel superior
    real de `GET /api/v1/artisans` y `GET /api/v1/pieces` (sección 8).

**No quedan `PROPOSED DECISION` abiertos en este documento.**

## 16. Explícitamente delegado a `SECURITY.md` o a trabajo futuro

Lo siguiente no es una decisión pendiente de este contrato, sino una
delimitación de alcance intencional:

**Delegado a `SECURITY.md`:**

- Algoritmo de verificación del token contra `token_hash` (sección 7).
- Implementación detallada de rate limiting, incluida la mecánica
  exacta detrás de `429` (secciones 7 y 10).
- Semántica exacta de indistinguibilidad anti-enumeración entre
  `authentic` y `unavailable` a nivel de transporte/tiempo de respuesta,
  y la distinción interna (inexistente/revocado/malformado) para
  auditoría (sección 7).
- Logging de intentos hacia `AUDIT_EVENT` (sección 7).
- Mecanismo de autenticación administrativa (sección 14).

**Trabajo futuro (backend/admin, no bloqueante para el MVP público):**

- Diseño concreto de paginación por cursor, cuando el catálogo lo
  justifique (sección 8).
- Validación administrativa que prevenga el estado inconsistente
  pieza-publicada/artesano-no-publicado (sección 9).
- Contrato administrativo completo: cuerpos de request/response,
  estrategia de identificador, paginación admin, roles, mapeo a
  `AUDIT_EVENT` (sección 14).
- Ampliación futura de la allowlist de `authenticity_metadata` cuando
  exista contenido real que lo justifique (sección 7).
