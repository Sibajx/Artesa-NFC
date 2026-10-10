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

Los endpoints públicos de las secciones 3–13 cuelgan de este prefijo, según
`ARCHITECTURE.md` §7. Gestión usa `/api/admin/v1` (§14) y los bytes públicos de
media se sirven bajo `/media/` (§14.3).

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
  `MEDIA_ASSET` público (sección 6): la primera foto activa con
  `role = hero`; si no hay, la primera foto activa de la pieza en su orden
  (2026-10); `null` si la pieza no tiene fotos.

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

### 6.1 Mapeo temporal de `url` en Sprint 3 (aprobado, no definitivo)

**Decisión aceptada por Alexis (Product Owner), cierre de Sprint 3,
2026-09-17:** mientras no exista una capa real de storage/CDN, el
backend deriva `url` con el mapeo determinista más simple posible:

```text
url = f"/media/{storage_path}"
```

Precisiones sobre esta decisión:

- `storage_path` **sigue sin exponerse como campo JSON**: ninguna
  respuesta pública contiene una clave `storage_path`. Lo que cambia es
  que, en Sprint 3, el *valor* de `storage_path` es recuperable a
  partir del *valor* de `url` (quitando el prefijo `/media/`), porque
  el mapeo actual es una concatenación directa, no una transformación
  opaca.
- Esto significa que, en la práctica, la convención interna de nombrado
  de rutas de almacenamiento (ej. `demo/artisans/{slug}/portrait.jpg`)
  es observable a través de `url` durante Sprint 3. Se acepta
  explícitamente esta exposición porque todo el contenido servido en
  Sprint 3 es el fixture ficticio de `docs/SPRINT_2.md`/`SPRINT_3.md`
  (sin datos reales de artesano/pieza todavía, `PROJECT.md` §11), por lo
  que no hay información sensible ni de producción en juego.
- Esta decisión **no reabre** la prohibición de la sección 6 de exponer
  `storage_path` como campo o detalle de infraestructura explícito
  (nombres de bucket, rutas de sistema de archivos del servidor,
  identificadores de objeto privados); solo acota, para Sprint 3
  específicamente, que el valor de `url` no se trata todavía como
  opaco frente al valor interno que lo originó.
- **Esto no es la arquitectura de medios final.** Antes de servir
  contenido real de artesano/pieza (`PROJECT.md` §11), se espera
  reemplazar este mapeo por una capa real de media/CDN (URLs firmadas,
  identificador opaco, o dominio de CDN dedicado) que deje de exponer
  la convención de nombrado interno. Esto puede hacerse sin cambiar el
  contrato de la columna `storage_path` en `DATA_MODEL.md` §2.5 ni la
  forma pública de `MEDIA_ASSET` (`type`, `role`, `url`, `alt_text`,
  `position`, `format` se mantienen) — solo cambia cómo se calcula el
  valor de `url`, lo cual es un cambio no disruptivo según la sección
  13.

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

### 7.1 Certificado original con la tarjeta del comprador (ADR-030 fase 3)

- `POST /api/v1/certificates/resolve`: `authenticity` suma `reported_stolen` (bool). Si es `true`, la pieza sigue siendo auténtica, pero la página muestra el aviso y no ofrece el original.
- `POST /api/v1/certificates/unlock` `{"token", "key", "pin"?}` y `POST /api/v1/certificates/claim` `{"token", "key", "email", "pin"}`. Con éxito responden `{"result": "unlocked", authenticity, piece, artisan, authenticity_metadata, ownership: {claimed, claimed_at, owner_email_masked, card_issued_at}}`.
- Cualquier rechazo es `200 {"result": "invalid" | "pin_required" | "reported_stolen"}`. `invalid` no distingue entre token, clave, PIN o tarjeta bloqueada. `pin_required` solo se devuelve con una clave correcta de una pieza reclamada.
- `429 too_many_attempts` (con `retry_after` en segundos): tras 5 fallos la tarjeta se bloquea 15 min, duplicando hasta 24 h, y hay un límite de 20 fallos por IP cada 15 min.
- `claim` valida antes con `422`: `invalid_email`, `invalid_pin` (6 dígitos) y `weak_pin`.
- Las dos rutas comparten con `resolve` el límite de 1 KB y `no-store`.

### 7.2 Paleta de la pieza para el certificado genérico (ADR-030 fase 4)

- `piece.visual_theme` (público, esquema abierto ADR-012) puede llevar `{"palette": ["#rrggbb", …], "palette_source": "auto" | "manual"}`: de 3 a 5 colores en minúsculas, el más presente primero. Las demás llaves se conservan.
- Cuándo se llena y quién lo cambia:
  - la primera foto que recibe la pieza llena la paleta `auto`, **sin cambiar** `updated_at`;
  - en Gestión, `POST /api/admin/v1/pieces/{id}/palette/generate` la recalcula desde la portada (`If-Match`; `409 no_cover_photo` o `unreadable_cover_photo`);
  - `POST /api/admin/v1/pieces/{id}/palette` `{"colors": [...]}` la fija a mano (`409 invalid_palette`);
  - ambas quedan auditadas como `piece.palette_set`.
- La web (`/c/{token}`) muestra la franja de colores y usa el color más saturado como acento. Si la paleta no tiene la forma esperada, se ignora y la página usa los colores del sitio.

### 7.3 Certificado original diseñado (ADR-030 fase 5)

- **Al abrir el original:** `POST /api/v1/certificates/unlock` y `/claim` añaden `design: {version, svg, approved_by_name, approved_at} | null`, el diseño publicado de la pieza. El SVG lo dibuja la API, con los textos escapados, sin referencias externas ni scripts. La web lo muestra dentro de un `<img>`.
- **Enlace de revisión del artesano:** la web lo abre en `/revision/#<token>`, con el token en el fragmento, así que nunca llega a un log.
  - `POST /api/v1/design-reviews/resolve` `{"token"}` → `{"status": "open", piece_name, artisan_name, version, expires_at, svg}` o `{"status": "unavailable"}`. Es la misma respuesta para un token desconocido, vencido o ya decidido.
  - `POST /api/v1/design-reviews/decision` `{"token", "decision": "approve" | "changes", "comment"?}` → `{"status": "recorded" | "unavailable"}`.
  - Las dos rutas comparten con `resolve` el límite de 1 KB y `no-store`.
- **API de Gestión** (rol Diseñador; los Custodios también lo tienen; los Editores reciben 403):

| Ruta | Qué hace |
|---|---|
| `GET /pieces/{id}/designs` | Lista las versiones |
| `GET /designs/{id}` | Una versión, con su SVG |
| `POST /designs/preview` `{params, version}` | Dibuja el SVG sin guardar nada |
| `POST /pieces/{id}/designs` | Crea un borrador; copia la última versión (`409 open_design_exists` si ya hay una abierta) |
| `PATCH /designs/{id}` `{params}` | Edita un borrador; si estaba en revisión, el enlace deja de servir (`409 design_frozen` si ya está aprobado) |
| `POST /designs/{id}/submit` | Lo pasa a revisión. **Única respuesta con `review_url`**, válido 14 días |
| `POST /designs/{id}/approve` `{name, medium, note}` | Registra a mano la aprobación del artesano |
| `POST /designs/{id}/publish` | Solo para un diseño aprobado; el publicado anterior pasa a `superseded` |
| `POST /designs/{id}/discard` | Borra una versión abierta: borrador, en revisión o aprobada sin publicar (la aprobación queda en la auditoría). Una versión publicada o reemplazada no se borra (`409 design_frozen`) |

- Todas las escrituras usan `If-Match` y quedan auditadas como `design.*`.
- `params`: `template` (`clasico` \| `greca` \| `constelacion`), `variant` (`claro` \| `oscuro`), `title`, `piece_name`, `artisan_name`, `public_code`, `quote` (hasta 240 caracteres), `palette` (de 3 a 5 colores) y `seed`.
- **Arte propio (fase 5b):**
  - `POST /certificate-art`, solo con rol de diseño. El cuerpo es la imagen misma: PNG, JPEG o WebP, hasta 8 MB, con su `Content-Type` y `X-Artesa-Admin: 1`. Responde `201 {id, mime_type, width, height}`.
  - El servidor la decodifica y la vuelve a codificar: no conserva metadatos ni datos extra, el lado mayor queda en 1000 px, es PNG si tiene transparencia y JPEG si no.
  - El `id` es el SHA-256 del resultado. La imagen no cambia ni se borra, así que una versión congelada se dibuja igual para siempre.
  - Errores: `415 unsupported_media_type`, `413 too_large`, `422 unsupported_type` / `image_too_large` / `empty_file`.
  - El diseño la usa con `params.art = {id, placement: "sello" | "encabezado" | "fondo", opacity: 0.05–0.6}`. Un `id` que no existe da `409 invalid_design`, y `art: null` quita el arte.
  - El SVG la incrusta como `data:` URI, armada en el servidor; los `params` solo llevan el hash.

### 7.4 Ventas y precio (P-026)

- **API pública:** `availability_status` puede ser `sold` ("Vendida"). El precio no se expone.
- **API de Gestión:**
  - `PATCH /pieces/{id}` acepta `price_cents` (≥ 0) y `price_currency` (`MXN` \| `USD`);
  - `POST /pieces/{id}/sale` `{sold_on, price_cents, currency, channel, sold_by, buyer_name?, buyer_contact?, note?}` con `If-Match` pone la pieza en `sold` (`409 already_sold`; `invalid_sale` si la fecha es futura);
  - `POST /pieces/{id}/sale/cancel` `{reason}` la regresa a `available` (`409 not_sold`);
  - `POST /pieces/{id}/availability` rechaza poner o quitar `sold` (`409 use_sale`);
  - el detalle de la pieza trae `price_cents`, `price_currency` y `sales` (de la más reciente a la más antigua).
- **Auditoría:** `piece.sold` y `piece.sale_cancelled`, sin los datos del comprador.

### 7.5 Autorización del artesano y cuentas (P-026 G3/G4)

- **Enlace de autorización:** la web lo abre en `/autorizacion/#<token>`, con el token en el fragmento.
  - `POST /api/v1/artisan-authorizations/resolve` `{"token"}` → `{"status": "open", full_name, artistic_name, place, biography, history, techniques, languages, public_contact, portrait, pieces: [{name, cover}], confirming, expires_at}` o `{"status": "unavailable"}`. Muestra **todo** lo que el sitio publica del artesano: la biografía completa, sin recorte, y solo sus piezas publicadas. `confirming` es `true` cuando se confirma por WhatsApp una autorización dada en persona. Los enlaces enviados antes del 2026-10-05 solo traen los primeros cinco campos; los demás llegan vacíos.
  - `POST /api/v1/artisan-authorizations/decision` `{"token", "decision": "authorize" | "changes" | "decline", "comment"?}` → `{"status": "recorded" | "unavailable" | "comment_required"}`:
    - `changes` ("Quiero cambios") exige un comentario de al menos 3 letras (`comment_required` si falta) y **no despublica nada**;
    - `decline` ("No autorizo") pasa a borrador, en la misma transacción, al artesano y a sus piezas publicadas. Lo audita el sistema (`artisan.unpublished`/`piece.unpublished` con `reason: authorization_declined`).
  - Las dos rutas tienen el límite de 1 KB y `no-store`.
  - Cuando la respuesta es `changes` o `decline`, el sistema avisa por correo a los dueños (`OWNER_EMAILS`) en segundo plano: el nombre del artesano, lo que escribió y un enlace a Gestión, sin el enlace del artesano ni su contacto. Sin correo configurado o si el envío falla, la respuesta queda registrada igual.
- **API de Gestión, autorización:**
  - `POST /artisans/{id}/authorization/request` → `{url, whatsapp, contact_name, artisan_name}`. Es la única respuesta con el enlace; vale 14 días y reemplaza al pendiente. Con una autorización **en persona** también se puede pedir: es la confirmación por WhatsApp, y la autorización sigue valiendo mientras el enlace está abierto. Con una autorización por WhatsApp responde `409 already_authorized`;
  - `POST /artisans/{id}/authorization/record` `{note}`, cuando el artesano autorizó en persona;
  - `POST /artisans/{id}/authorization/revoke` `{note}`;
  - el detalle del artesano trae `validation_whatsapp`, `validation_contact_name`, `authorization` y `last_answer`. `last_answer` es la última respuesta "Quiero cambios" o "No autorizo", con el comentario en `note`, hasta que un enlace nuevo la reemplace;
  - el Resumen agrega `authorizations_with_changes_requested`;
  - publicar sin autorización responde `409 authorization_missing`.
- **API de Gestión, cuentas** (solo el dueño; los demás reciben 403):
  - `GET /accounts`;
  - `POST /accounts` `{email, role, note?}`;
  - `POST /accounts/{email}/role` `{role}`;
  - `POST /accounts/{email}/remove`;
  - `POST /accounts/sync`.

  Todas responden `{data, cloudflare, sync_configured}`, donde `cloudflare` es `synced`, `not_configured`, `unchanged` o el texto del error. Las cuentas de `ADMIN_EMAILS` aparecen como fijas (`409 fixed_account`).

### 7.6 Exportar a CSV (P-026 G11)

- `GET /api/admin/v1/exports/pieces.csv` → `text/csv; charset=utf-8` con BOM, `Content-Disposition: attachment; filename="artesanfc-piezas-AAAA-MM-DD.csv"` y `no-store`, como toda la API de Gestión.
- Una fila por pieza fuera de la papelera, ordenadas por artesano y código. No aplica los filtros de la lista.
- **Columnas para cualquier cuenta de Gestión:** código, pieza, artesano, publicación, disponibilidad, precio y moneda, ubicación y lugar actuales (§7.7), técnica, materiales, año, fecha, precio y canal de la venta activa, creada y actualizada.
  - Los estados van en español.
  - Las fechas van en hora de México (UTC−6).
  - El dinero va con dos decimales.
- **Columnas extra para custodios** (ADR-030): certificado y versión, chip y modelo, tarjeta, con dueño, diseño del certificado y reportada como robada.
- **Datos que no salen:** el nombre y el contacto del comprador.
- **Celdas de texto:** si empiezan con `=`, `+`, `-`, `@`, tabulador o retorno de carro, se les antepone un apóstrofo para que la hoja de cálculo no las evalúe como fórmula.
- **Auditoría:** `export.pieces` con `{rows, custody_columns}`.

### 7.7 Ubicación física de la pieza (P-026 G12)

- **Solo Gestión:** la API pública no expone la ubicación.
- `POST /api/admin/v1/pieces/{id}/location` `{location, place?, moved_on, note?}` con `If-Match` registra un movimiento y responde el detalle de la pieza:
  - `location` es `taller`, `bodega`, `tienda`, `exhibicion`, `transito`, `entregada` u `otro`;
  - `place` (hasta 120 caracteres) dice cuál tienda, feria o museo;
  - `moved_on` no puede ser futura (`409 invalid_location`);
  - una pieza en la papelera responde `409 trashed`.
- **Historial:** los movimientos no se editan; una corrección es un movimiento nuevo. El detalle de la pieza trae `locations` del más reciente al más antiguo, y el primero es la ubicación actual.
- **Purga:** los movimientos se borran junto con la pieza solo cuando se purga de la papelera un borrador que nunca fue público.
- **Auditoría:** `piece.moved` con `{location_id, from, to, place, moved_on}`.
- **Tabla:** `piece_location`, creada por la migración `57cc7fb123cb` (additive).

## 8. Listados, filtrado y paginación

El fixture determinista del repositorio tiene 2 artesanos y 4 piezas
(`PROJECT.md` §11); no describe el contenido real. **Aprobado para el MVP:** no
se implementa paginación real; se define una envoltura simple y
forward-compatible.

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
| `413 Payload Too Large` | Solo `POST /api/v1/certificates/resolve`: cuerpo de más de 1024 bytes (`Content-Length` mayor, o cuerpo sin `Content-Length`/chunked que lo supera al llegar). Se rechaza sin leer el resto. Código `payload_too_large`; lleva `Cache-Control: no-store` y CORS del origen permitido (`SECURITY.md` §5.6). |
| `422 Unprocessable Entity` | Errores de validación de entrada de la solicitud — body, parámetros de query y parámetros de path — incluyendo tipo incorrecto, campo faltante, o valor fuera del rango/enum esperado (ej. `token` ausente en `certificates/resolve`, o `availability_status=xyz` en un filtro de query). Corresponde al comportamiento estándar de validación de FastAPI/Pydantic; no se requiere convertir estos casos a `400`. |
| `429 Too Many Requests` | Rate limiting activado (mecanismo definido en `SECURITY.md`; el código y la forma de respuesta sí son parte de este contrato). |
| `500 Internal Server Error` | Error no controlado del servidor. En `certificates/resolve` y en `/api/admin` lleva `Cache-Control: no-store`, y lleva CORS si el origen está permitido (N-02). |
| `503 Service Unavailable` | La base de datos no está disponible: no hay conexión, o el servidor de base de datos se está apagando o rechaza conexiones. Código `service_unavailable`, con `Retry-After: 30`. Un error de una consulta, como un timeout de bloqueo o un constraint, sigue siendo `500` (N-02). |

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

### Forma de error (para `400`, `404`, `405`, `413`, `422`, `429`, `500`, `503`)

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
  `payload_too_large`, `rate_limited`, `internal_error`).
- `message`: texto legible, no sensible, sin detalles de
  implementación.
- **Nunca** se incluyen stack traces, mensajes de excepción de
  base de datos, nombres de tabla/columna, ni rutas de archivo del
  servidor.

### Fuera del contrato: `GET|HEAD /health`

`/health` es un endpoint **operativo**, no parte de la API pública: el
frontend no debe consumirlo. Responde `200`
`{"status": "ok", "database": "connected"}` si la API alcanza su base de datos
y `503` `{"status": "unavailable", "database": "unavailable"}` si no, siempre
con `Cache-Control: no-store` y sin ningún detalle de la falla; `HEAD /health`
devuelve el mismo estado sin cuerpo. Tampoco forman parte del contrato
`/docs`, `/redoc` ni `/openapi.json`: solo existen con `APP_ENV=local` o
`test`. Ver `docs/OPERATIONS.md`.

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
| **No expuesto por los endpoints públicos de este documento** | `nfc_tag.physical_uid`, UUIDs internos (`artisan.id`, `piece.id`, `certificate.id`, `media_asset.id`, `nfc_tag.id`), `audit_event.*`, `actor_id`/`actor_type`, rutas de almacenamiento/bucket internas | Ningún endpoint público de las secciones 3–9 expone estos campos. La superficie interna de Gestión se define por separado en §14 y se protege con Cloudflare Access + `ADMIN_EMAILS`. Sus eventos de auditoría de media sí incluyen `storage_path` y sha256 del original como metadatos internos. |
| **Filtrado por publicación** | `artisan`/`piece` con `publication_status != published`, o una pieza `published` cuyo artesano no lo está (sección 9) | Nunca aparecen en endpoints públicos; no existe parámetro para forzar su inclusión desde fuera de la API administrativa. |

Esta tabla es la referencia única para auditar cualquier endpoint nuevo
que se agregue a este documento en el futuro: si un campo no aparece
aquí como público (en alguna de sus tres formas), no se expone sin antes
actualizar esta sección. La prohibición absoluta de `token_hash`/token en texto
plano también se extiende al contrato administrativo de §14.

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

## 14. API administrativa

### 14.0 Historia: namespace conceptual descartado

El bloque siguiente conserva la propuesta anterior para explicar por qué fue
reemplazada. El contrato vigente empieza en §14.1.

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

### 14.1 Fase 1 de Gestión (ADR-029, 2026-09-28)

El texto anterior de esta sección se conserva como historia. Desde ADR-029:

- **Namespace:** `/api/admin/v1`, no `/api/v1/admin`: el admin no debe quedar
  dentro del prefijo público que permite la regla A de Cloudflare.
- **Autenticación:** JWT de Cloudflare Access en la cabecera
  `Cf-Access-Jwt-Assertion`, verificado por la API (RS256, audiencia, emisor,
  expiración) más la allowlist `ADMIN_EMAILS`. Sin cabecera o con un token
  inválido: `401 {"code": "unauthenticated"}`. Con un email fuera de la lista:
  `403 {"code": "forbidden"}`. Si no se pueden obtener las claves:
  `503 {"code": "auth_unavailable"}`. Sin configuración admin, `404` idéntico al
  de una ruta inexistente.
- **Todas** las respuestas llevan `Cache-Control: no-store`. No hay CORS para
  el admin.
- Los identificadores en la URL son los UUID internos. Un UUID mal formado da
  `422 validation_error`; uno inexistente, el `404` estándar.

| Método y ruta | Respuesta |
|---|---|
| `GET /api/admin/v1/me` | `{"email", "roles"}` de la identidad verificada; `roles` ⊆ `editor`, `designer`, `custodian` (ADR-030) |
| `GET /api/admin/v1/custody/pieces` | **Solo Custodios** (403 + `audit_event` `custody.denied` para los demás): cada pieza fuera de la papelera con `certificate_status`, `certificate_version`, `tag_status`, `tag_chip` y `ready_to_certify`. Nunca `token_hash` ni `physical_uid` |
| `GET /api/admin/v1/custody/pieces/{id}/state` | Solo Custodios. Estado de certificación de la pieza: certificado activo, chips (`uid`, estado), `recommended_action` y bloqueos (`issue_blockers`, `rotate_blockers`, `lock_blockers`) |
| `POST …/custody/pieces/{id}/issue` `{"uid"}` | Registra el chip, lo asigna y emite el certificado en una transacción; autochequeo de `resolve`. **Única respuesta con la URL del certificado** (ADR-030), `no-store`, nunca registrada |
| `POST …/custody/pieces/{id}/rotate` `{"reason", "uid"?}` | Revoca y reemite; `uid` = chip nuevo, sin `uid` = el mismo chip. Devuelve la URL nueva |
| `POST …/custody/pieces/{id}/program` `{"tag_id", "uid"}` | Tras grabar **y leer de vuelta**: el `uid` leído debe ser el registrado (`409 uid_mismatch`) |
| `POST …/custody/pieces/{id}/lock` `{"uid"}` | Tras el bloqueo físico confirmado por el navegador |
| `POST …/custody/pieces/{id}/revoke` `{"reason"}` | Revoca y retira los chips |
| `POST …/custody/pieces/{id}/tags/{tag_id}/release` `{}` | Libera el UID de un chip de la pieza dado de baja (`replaced`/`retired`) que **nunca se bloqueó**, para volver a registrarlo. La fila queda como historial con `physical_uid = NULL`; el UID pasa a sus `notes` (`release-uid`) y a la auditoría (`custody.uid_released`). `409 tag_not_retired` / `tag_was_locked` / `uid_already_released` / `tag_not_available`. El estado (`GET …/state`) lista los liberables en `releasable_tags` |
| `POST …/custody/pieces/{id}/card/issue` `{}` | ADR-030 fase 3. Genera la tarjeta del comprador (requiere certificado activo; `409 card_exists` si ya hay una). **Única respuesta con la clave**: `{"key": "XXXX-XXXX-XX", "public_code"}`, `no-store`, una sola vez |
| `POST …/card/replace` · `…/transfer` `{"note"}` | Tarjeta perdida / pieza vendida: la tarjeta anterior queda `replaced` y se devuelve una clave nueva. `transfer` además libera el reclamo |
| `POST …/card/block` · `…/card/unblock` · `…/claim/release` · `…/stolen` · `…/stolen/clear` `{"note"}` | Devuelven el estado. `note` (5–500) es obligatoria: la prueba que revisó el Custodio; queda en `audit_event` |
| `GET /api/admin/v1/artisans?publication_status=&q=` | `ListEnvelope` de `{id, slug, full_name, artistic_name, publication_status, piece_count, updated_at}`; **incluye borradores y archivados**; orden `updated_at` desc |
| `GET /api/admin/v1/artisans/{id}` | todos los campos del artesano (incluido `public_contact`), `media` (con `id` y `status`) y **todas** sus piezas |
| `GET /api/admin/v1/pieces?publication_status=&artisan_id=&q=` | `ListEnvelope` de `{id, slug, public_code, name, artisan_id, artisan_slug, publication_status, availability_status, updated_at}` |
| `GET /api/admin/v1/pieces/{id}` | (ADR-030) `certificates` y `nfc_tags` solo para Custodios; para los demás llegan vacíos con `custody_visible: false`. |
| `GET /api/admin/v1/pieces/{id}` (detalle) | todos los campos de la pieza, `publicly_visible` (pieza **y** artesano publicados, §9), `artisan`, `media`, historial de `certificates` (`id, status, version, issued_at, revoked_at, revocation_reason, created_at`) y de `nfc_tags` (`id, status, chip_model, programmed_at, locked_at, created_at`) |
| `GET /api/admin/v1/audit-events?entity_type=&entity_id=&limit=` | eventos más recientes primero (`limit` 1–200, por defecto 50) |

`q` busca sin distinguir mayúsculas; `%` y `_` se tratan como texto literal.

**Nunca** se exponen, tampoco aquí: `certificate.token_hash`, el token,
`nfc_tag.physical_uid` ni `audit_event.ip_address`. El endpoint de auditoría
devuelve `metadata`; para `media.uploaded` contiene el `storage_path` interno y
el sha256 del original. Emitir, rotar o revocar certificados y programar tags
sigue siendo solo por CLI (ADR-026).

### 14.2 Fase 2 de Gestión: escrituras de contenido (ADR-029)

Además de la identidad de §14.1, **toda escritura** exige:
- la cabecera `X-Artesa-Admin: 1`;
- un cuerpo `application/json` (si no, `415 unsupported_media_type`);
- un `Origin`, cuando el navegador lo envía, igual al host de la petición.

Si falla la cabecera o el `Origin`: `403 forbidden`. Es la defensa contra CSRF:
Access agrega su cabecera a cualquier petición que lleve la cookie de sesión.

Todo cambio sobre un registro existente exige **`If-Match: <updated_at>`**, el
valor que el cliente cargó. Sin él: `428 precondition_required`. Con un valor
que no es fecha: `400`. Si el registro cambió: `412 stale`. Un campo
desconocido en el cuerpo da `422`.

| Método y ruta | Efecto |
|---|---|
| `POST /api/admin/v1/artisans` | Crea en `draft`. `slug` es opcional: se genera del nombre y es único (`-2`, `-3`…). Devuelve el detalle de §14.1 con **201** |
| `PATCH /api/admin/v1/artisans/{id}` | Cambia solo los campos enviados. `slug` solo se puede cambiar en `draft` |
| `POST /api/admin/v1/artisans/{id}/{publish\|unpublish\|archive\|restore}` | Cuerpo `{}` o `{"reason": "..."}`. `archive` se rechaza si el artesano tiene piezas publicadas |
| `POST /api/admin/v1/pieces` | Crea en `draft`. Si no se envía `public_code`, se genera `ANFC-XXXXXX` (alfabeto sin 0/O/1/I). El artesano no puede estar archivado |
| `PATCH /api/admin/v1/pieces/{id}` | `slug`, `public_code` y `artisan_id` solo se pueden cambiar en `draft` |
| `POST /api/admin/v1/pieces/{id}/{publish\|unpublish\|archive\|restore}` | `archive` se rechaza si hay un certificado **activo**: primero se revoca por CLI |
| `POST /api/admin/v1/pieces/{id}/availability` | `{"availability_status": "available\|reserved\|exhibited\|archived"}` |

Transiciones permitidas: `draft → published`, `published → draft`,
`draft|published → archived` y `archived → draft`. Cualquier otra da
`409 invalid_transition`.

**No hay DELETE ni PUT.** Los códigos `409` son: `duplicate` (con `field`),
`draft_only`, `invalid_transition`, `incomplete`, `has_published_pieces`,
`active_certificate`, `unknown_artisan` y `archived_artisan`.

**Auditoría:** cada cambio que aplica algo inserta **en la misma transacción**
un `audit_event` con `actor_email`, la IP y, en `metadata`, un diff
`{campo: {from, to}}` o la transición con su motivo. Las acciones son
`artisan.created`, `.updated`, `.published`, `.unpublished`, `.archived`,
`.restored`, lo mismo con `piece.*`, y `piece.availability_changed`.

Detalles del registro:
- `public_contact` **nunca** va en el diff: se anota solo `{"changed": true}`;
- un cambio sin efecto no toca `updated_at` ni deja evento;
- una escritura rechazada no deja evento.

### 14.3 Fase 4 de Gestión: medios (docs/MEDIA.md, 2026-09-29)

**Subida:** `POST /api/admin/v1/{artisans|pieces}/{id}/media?role=<rol>&alt_text=<texto>`.
- El **cuerpo es el archivo** (sin multipart), con su propio `Content-Type`:
  `image/jpeg`, `image/png`, `image/webp`, `video/mp4`, `model/gltf-binary` o
  `application/octet-stream`. Otro tipo: `415`.
- Mismo guard CSRF que §14.2: `X-Artesa-Admin: 1` y `Origin` del mismo host.
- Límite del cuerpo: 25 MB (`413 too_large`, se corta al leer, sin confiar en
  `Content-Length`).
- El tipo real se deduce **de los bytes**, nunca del cliente.

| Tipo | Qué se publica | Rechazos (`422` salvo tamaño) |
|---|---|---|
| Foto JPEG/PNG/WebP | Re-codificada: EXIF y GPS fuera, rotación aplicada, lado mayor ≤ 1600 px, JPEG progresivo q80 | `unsupported_type`, `image_too_large` (> 50 MP), `alt_text_required` |
| Video MP4 | Tal cual, **sin re-codificar** | `video_has_audio`, `video_has_location`, `invalid_video`; > 4 MB: `413` |
| Modelo GLB | Tal cual (cabecera glTF 2.0 y longitud verificadas) | `invalid_model`; > 8 MB: `413` |

Roles: artesano `portrait` (foto), `process` y `gallery` (foto o video); pieza
`hero` y `detail` (foto), `gallery` y `process` (foto o video), `model_3d` (GLB).
Otro rol: `invalid_role`. Tipo que no corresponde: `wrong_type_for_role`.
Dueño archivado: `409 archived`. Sin `MEDIA_ROOT` configurado: `503 media_not_configured`.

Respuesta **201**: `{id, status, updated_at, media}` (`media` con la forma pública de §6).
Archivos: el original, byte a byte, en `originales/{artesano}/{_artesano|pieza}/`
(0600, nunca servido); el derivado en `publico/{artesanos|piezas}/{slug}/{rol}-{nn}.{ext}`,
**nunca sobrescrito**.

| Método y ruta | Efecto |
|---|---|
| `PATCH /api/admin/v1/media/{id}` | JSON `{"alt_text"?, "position"? (0–999), "role"?}` con `If-Match`. Una foto no puede quedar sin `alt_text`. `role` debe ser un rol del dueño que acepte el tipo del archivo (`422 invalid_role` / `wrong_type_for_role`); el nombre del archivo publicado no cambia |
| `POST /api/admin/v1/media/{id}/{archive\|restore}` | JSON `{}` con `If-Match`. Archivar lo quita de la API pública; el archivo **no** se borra |
| `DELETE /api/admin/v1/media/{id}` | JSON `{}` con `If-Match` → `204`. Solo para medios que **nunca pudieron ser públicos** (`deletable: true`); si no, `409 may_have_been_public` y hay que archivar. Borra la fila, el archivo publicado y el original; deja una lápida vacía `{rol}-{nn}.deleted` para que ese número no se reutilice (2026-10, decisión del PO) |

**Auditoría** (`entity_type = "media_asset"`): `media.uploaded` (dueño, rol,
tipo, `storage_path`, bytes, sha256 del original), `media.updated` (diff),
`media.archived`, `media.restored`, `media.deleted` (dueño, rol, `storage_path`,
si se borró el original). Las listas `media` del detalle admin (§14.1) traen
`updated_at` y `deletable`: `true` solo si la cadena de dueños (artesano; o
pieza y artesano) no está publicada ahora **y** no tuvo ninguna transición de
publicación desde que se subió el medio (conservador: publicar y despublicar
después de subir ya lo vuelve solo archivable).

**Servir:** `GET|HEAD /media/{artesanos|piezas|sitio}/{slug}/{nombre}.{ext}` desde
`publico/`. Solo rutas con esa forma exacta (minúsculas, extensiones de la lista
blanca); cualquier otra, `404` sin tocar el disco. Cabeceras:
`Cache-Control: public, max-age=31536000, immutable`, `nosniff` y
`Access-Control-Allow-Origin: *`. Esta última es una excepción acotada a bytes
públicos: `<model-viewer>` pide el GLB con CORS, y Cloudflare guarda una sola
copia por URL sin mirar `Vary: Origin`. La regla A de producción fue ampliada y
verificada para `GET|HEAD /media/*` el 2026-09-30 (`OPERATIONS.md` §8).

### 14.4 Hero por temporada (P-028, 2026-10-09)

**Público.** `GET /api/v1/hero` → `{"data": null}` o `{"data": {"slug", "name", "reason": "forced"|"date"|"default", "video": {"mp4", "webm"|null}, "poster"}}`. Las URLs son rutas `/media/hero/{slug}/{hash}.{mp4|webm|jpg}` contra el origen de la API. `Cache-Control: public, max-age=300`. `data: null` = el sitio conserva su hero incluido. Elige, en orden: la temporada **forzada** (si no pasó su fecha de fin), una **publicada** cuyo rango anual cubre hoy en `America/Mexico_City` (si varias, la que empezó más tarde), el hero normal **publicado**.

**Gestión** (`/api/admin/v1/hero`, solo los roles `hero` y `designer_hero` (Diseñador y Hero) y el dueño; los demás reciben 403). Escrituras con `X-Artesa-Admin: 1` y JSON; sin `If-Match` (dos personas, cambios simples). Todas devuelven el estado completo.

| Método y ruta | Qué hace |
|---|---|
| `GET /hero` | Estado: `today`, `ffmpeg_available`, `media_enabled`, `live_id`, `live_reason`, `campaigns[]` (`status`: `no_video`, `processing`, `error`, `draft`, `live`, `scheduled`). |
| `POST /hero/campaigns` | Crea una temporada (`name`, `start_month/day`, `end_month/day`; se repite cada año). |
| `PATCH /hero/campaigns/{id}` | Cambia nombre y/o fechas (el hero normal no tiene fechas). |
| `DELETE /hero/campaigns/{id}` | Borra la temporada y sus archivos (no el hero normal ni la forzada). |
| `POST /hero/campaigns/{id}/video` | Sube el video **como cuerpo** (`video/mp4`, `video/webm` o `video/quicktime`, hasta 200 MB), con `?start=<segundos>` opcional (0–3600, por defecto 0). 202: se procesa en segundo plano con `ffmpeg` (16:9 centrado, 20 s desde `start`, sin audio, ≤1080p, MP4 + WebM + portada; el MP4 no pasa de 14 MB). Si `start` cae al final del video o fuera de él, la campaña queda en `error` («Ese segundo…»). Mientras procesa o si falla, el video anterior sigue visible. El cuerpo se escribe a disco por partes. |
| `POST /hero/campaigns/{id}/publish` · `/unpublish` | Requiere un video listo. |
| `POST /hero/force` `{campaign_id, until?}` · `DELETE /hero/force` | Muestra una temporada para todos ya, con fin opcional; solo una a la vez. |

Errores: `no_video`, `already_processing`, `ffmpeg_unavailable`, `unsupported_media_type`, `too_large` (413), `not_a_video`, `encode_failed`, `too_heavy`, `invalid_date`, `invalid_name`, `default_campaign`, `forced_campaign`. Auditoría: `hero.*`.

### 14.5 Imágenes del sitio (P-029, 2026-10-10)

Fotos fijas de la página pública que el dueño y los roles `hero` y `designer_hero` pueden cambiar desde Gestión → Hero → «Imágenes del sitio». Cada **ranura** es un lugar de la página; hoy solo existe `collection-entry` («Entrada a la colección», proporción 4:5, hasta 1200×1500). Una ranura sin foto conserva la imagen provisional incluida en el sitio.

**Público.** `GET /api/v1/site-images` → `{"data": {"<ranura>": {"avif", "webp", "jpg", "width", "height"}}}`; solo aparecen las ranuras con foto. Las URLs son rutas `/media/sitio/{ranura}/{hash}.{avif|webp|jpg}` contra el origen de la API. `Cache-Control: public, max-age=300`.

**Gestión** (`/api/admin/v1/hero/site-images`, mismo rol que las temporadas; los demás reciben 403):

| Método y ruta | Qué hace |
|---|---|
| `GET /hero/site-images` | Estado: `media_enabled` y `slots[]` (`slot`, `label`, `ratio`, `image` o `null`). |
| `POST /hero/site-images/{ranura}` | Sube la foto **como cuerpo** (`image/jpeg`, `image/png` o `image/webp`, hasta 25 MB, con `X-Artesa-Admin: 1`). Se recorta al centro a la proporción de la ranura, se limita a su ancho máximo y se guarda en AVIF + WebP + JPEG sin metadatos. Mínimo 600 px de ancho en esa proporción. 200 con el estado completo. |
| `DELETE /hero/site-images/{ranura}` | Vuelve a la imagen provisional y borra los archivos. |

Errores: `unsupported_type` (422), `empty_file` (422), `too_small` (422), `image_too_large` (422), `too_large` (413), `unsupported_media_type` (415), `avif_unavailable` (503), `media_unavailable` (503), `not_found` (404, ranura desconocida).

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
11. La propuesta inicial dejó el namespace administrativo como conceptual. Fue
    reemplazada por ADR-029 y el contrato vigente de §§14.1–14.3.

Correcciones de consistencia adicionales aplicadas en esta revisión
(2026-09-16), no `PROPOSED DECISION` sino ajustes de redacción/alcance:

12. La restricción "nunca expuesto, público o administrativo" se acotó:
    solo `token_hash`/token en texto plano son absolutamente prohibidos en toda
    superficie. Los UUID y eventos internos están fuera de la API pública y se
    exponen a Gestión solo según §§11 y 14; `physical_uid` permanece omitido.
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
- Requisitos de seguridad de Cloudflare Access y la validación adicional de la
  API administrativa (sección 14; detalle en `SECURITY.md` §9).

**Trabajo futuro (backend/admin, no bloqueante para el MVP público):**

- Diseño concreto de paginación por cursor, cuando el catálogo lo
  justifique (sección 8).
- Validación administrativa que prevenga el estado inconsistente
  pieza-publicada/artesano-no-publicado (sección 9).
- Paginación administrativa y roles separados, si el piloto demuestra que son
  necesarios. Los cuerpos, identificadores y mapeos actuales están congelados
  en §§14.1–14.3.
- Ampliación futura de la allowlist de `authenticity_metadata` cuando
  exista contenido real que lo justifique (sección 7).
