# ArtesaNFC — Seguridad (MVP)

**Estado:** Aprobado para MVP
**Issue:** #5 — docs: create SECURITY.md
**Dominio de referencia:** `artesanfc.com` / `api.artesanfc.com`
**Fecha de referencia:** 2026-09-16

## 0. Propósito y alcance

Este documento define el **modelo de seguridad** del MVP de ArtesaNFC:
qué se protege, de qué, y qué comportamiento se exige al backend, a la
infraestructura y al flujo de certificado/NFC. **No implementa** estos
requisitos (no hay código, configuración de Nginx ni scripts aquí); es
la referencia contra la que se valida la implementación futura.

Este documento **no rediseña**:

- `docs/DATA_MODEL.md` — el esquema de datos ya aprobado.
- `docs/API_CONTRACT.md` — el contrato de interfaz ya aprobado,
  incluyendo `authenticity.status = authentic | unavailable` en
  `POST /api/v1/certificates/resolve`.

Cuando una necesidad de seguridad chocaría con algo ya congelado en esos
dos documentos, se marca explícitamente como `CROSS-DOCUMENT CHANGE
REQUIRED` en vez de modificarlos aquí (ver sección 20).

## 1. Modelo de amenazas

### 1.1 Activos a proteger

| Activo | Por qué importa |
|---|---|
| Token privado del certificado (texto plano, en tránsito/uso) | Es el único credential de acceso al certificado (ADR-007, ADR-008). |
| `certificate.token_hash` | Su filtración (ej. volcado de base de datos) no compromete tokens no filtrados individualmente (SHA-256 sin clave sobre 256 bits de entropía, sección 3), pero expone qué certificados existen a nivel interno; se protege igual que el resto de la base de datos. |
| Estado de autenticidad del certificado (`active`/`revoked`) | Determina si una pieza es presentada como auténtica; su integridad es el propósito central del producto. |
| Acceso administrativo | Permite crear/editar artesanos, piezas, NFC y certificados, y revocar certificados (`PROJECT.md` §5). |
| Disponibilidad del backend/API | Sin ella no hay descubrimiento público ni resolución de certificados. |
| `AUDIT_EVENT` | Es la fuente de verdad para investigar incidentes; su integridad importa tanto como la de los datos que audita. |
| IDs internos y metadatos (`id` UUID, `nfc_tag.physical_uid`, rutas de almacenamiento) | No son secretos por sí mismos, pero facilitan correlación/enumeración si se exponen innecesariamente (ADR-008). |
| Credenciales y secretos de servidor (`.env`, contraseñas de DB, claves administrativas futuras) | Su filtración compromete múltiples activos anteriores a la vez. |
| Integridad de datos de artesano/pieza | Es contenido editorial y de procedencia; su alteración no autorizada daña la confianza del producto. |

### 1.2 Actores de riesgo / escenarios realistas

- Alguien copia o comparte la URL de un certificado (screenshot, reenvío,
  publicación) — esto **no** es un ataque al sistema, es un uso
  posible del producto; se documenta como no-objetivo en la sección 21.
- Adivinación o enumeración de tokens (fuerza bruta dirigida contra
  `certificates/resolve`).
- Fuerza bruta genérica / scraping automatizado contra endpoints
  públicos.
- Solicitudes malformadas o maliciosas contra la API (payloads
  inválidos, inyección, fuzzing).
- Acceso administrativo no autorizado (credenciales débiles,
  credenciales filtradas, sesión/token robado).
- Variables de entorno o secretos filtrados (commit accidental,
  servidor comprometido, log con secretos).
- Manipulación física de tags NFC: reprogramación, reemplazo o clonado
  de un tag existente.
- Reutilización ("replay") de una URL de certificado válida — nota: por
  diseño, una URL de certificado válida **debe** poder reutilizarse
  mientras el certificado esté `active` (el poseedor físico consulta el
  certificado repetidamente); "replay" como amenaza aquí se refiere a
  que un tercero que obtuvo la URL sin poseer la pieza física puede
  reutilizarla igual — ver sección 21 sobre qué no se garantiza.
- Scraping/abuso automatizado contra listados públicos.
- Errores accidentales de un operador/administrador (revocar el
  certificado equivocado, publicar datos incompletos).
- Servidor o cuenta de despliegue comprometidos (acceso root, cuenta de
  GitHub/Cloudflare comprometida).

### 1.3 Qué NO garantiza el sistema (declaración temprana del modelo)

El sistema autentica el **registro digital respaldado en base de
datos** (el certificado y su estado), no la imposibilidad física de
clonar un tag NFC. NTAG213 es un chip de memoria NFC estándar sin
capacidades criptográficas de anti-clonación; un atacante decidido con
acceso físico al tag puede leer y duplicar su contenido (la URL). La
seguridad del sistema depende de la aleatoriedad/entropía del token y
de los controles del backend, **no** de que el hardware NFC sea
inclonable. Esto se desarrolla en la sección 8 y se reitera en la
sección 21 para que no quede como una promesa implícita en ningún otro
punto de este documento.

## 2. Tokens de certificado privado

Principios ya aprobados (ADR-007, ADR-008, `DATA_MODEL.md` §2.3):

- El token se genera con un generador aleatorio criptográficamente
  seguro (CSPRNG).
- No se usan algoritmos caseros (primos, secuencias, IDs) como
  mecanismo principal de seguridad.
- `piece.id` (UUID) ≠ `piece.public_code` ≠ `nfc_tag.physical_uid` ≠
  token privado del certificado — cuatro conceptos distintos.
- El token en texto plano nunca se persiste; solo se almacena
  `token_hash`.
- El token debe tener alta entropía e ser impráctico de adivinar.

### 2.1 Formato y entropía recomendados

**Recomendación de este documento:**

- **256 bits (32 bytes) de aleatoriedad**, generados con el CSPRNG del
  lenguaje/runtime del backend (ej. `secrets.token_bytes(32)` en
  Python).
- Codificación **Base64 URL-safe sin padding** (alfabeto
  `A-Za-z0-9-_`, sin `=`), resultando en un token de 43 caracteres.
  Alternativa aceptable: codificación hexadecimal (64 caracteres) si
  se prefiere legibilidad/debug sobre longitud de URL; Base64 URL-safe
  es la recomendación por defecto por ser más corto y seguro para URL.
- El token se coloca directamente en la ruta del certificado (sección
  9): `https://artesanfc.com/c/{token}`, siguiendo el diseño ya
  aprobado (`PROJECT.md` §8, `ARCHITECTURE.md` §6). **Aclaración
  importante:** colocar el token en el path en vez de en un parámetro
  de query **no es, por sí mismo, más seguro frente a logging**. Tanto
  el path como el query string de una URL pueden quedar registrados por
  reverse proxies, servidores web, sistemas de monitoreo, el historial
  del navegador, o herramientas de analítica mal configuradas — ninguna
  de esas superficies distingue de forma inherentemente más segura un
  segmento de path de un parámetro de query. La seguridad real de esta
  URL depende de controles explícitos, no de la elección path-vs-query:
  redacción/exclusión explícita del token en logs de acceso (sección
  7, sección 13), ausencia de analítica que capture el token o la URL
  completa, `Referrer-Policy: no-referrer` en la página de certificado,
  `noindex`, ausencia en el sitemap, y HTTPS obligatorio — todos ya
  requeridos en esta sección.
- 256 bits de entropía hacen la adivinación/fuerza bruta
  computacionalmente inviable incluso sin rate limiting, que igual se
  define en la sección 6 como capa adicional (defensa en profundidad).

**Aprobado como estándar del MVP** (ya no es una decisión pendiente):
256 bits de aleatoriedad generados con el CSPRNG del sistema
operativo/lenguaje, codificados en Base64 URL-safe sin padding. No se
usan algoritmos propios basados en primos, secuencias ni ninguna otra
criptografía casera (ADR-007) — el token es puro output de CSPRNG, sin
transformación adicional que reduzca su entropía efectiva.

## 3. Hash y verificación del token

`DATA_MODEL.md` §2.3 deja deliberadamente el algoritmo exacto para este
documento. `certificate.token_hash` es el único valor persistido; el
token en texto plano solo existe en tránsito (generación → entrega para
programar el NFC → cada request de resolución) y nunca en la base de
datos.

### 3.1 Enfoque aprobado para el MVP

**Aprobado:** `token_hash = SHA-256(token)`, sin clave/pepper.

Motivo (reemplaza la recomendación anterior de HMAC-SHA-256 con
pepper):

- El token ya contiene 256 bits de entropía generados por CSPRNG — no
  es una contraseña humana con entropía baja.
- El "guessing" offline de un token de 256 bits correctamente generado
  es computacionalmente inviable, con o sin clave adicional en el hash.
- Argon2/bcrypt son innecesarios para este modelo de amenaza: existen
  para resistir diccionarios contra contraseñas de baja entropía, no
  contra aleatoriedad de 256 bits.
- Un HMAC/pepper añadiría manejo de claves y complejidad de rotación
  (sección 3.3, ahora eliminada) sin beneficio de seguridad
  significativo para el MVP. **Aclaración sobre qué implica realmente
  la filtración de `token_hash`:** poseer `token_hash` de un registro
  específico **no** permite a un atacante llamar exitosamente a
  `certificates/resolve` enviando ese hash como si fuera el `token` —
  el endpoint recibe un valor y le aplica `SHA-256()` de nuevo
  (sección 3.2), por lo que enviar el hash directamente produce un
  digest distinto que no coincide con ningún registro. Para obtener
  una resolución `authentic`, el atacante necesitaría el **token en
  texto plano** original, y recuperar ese token de 256 bits a partir
  de su digest SHA-256 permanece computacionalmente inviable
  (propiedad de resistencia a preimagen de SHA-256). La consecuencia
  principal de un volcado de base de datos filtrado es la exposición de
  registros internos y de los hashes almacenados — qué certificados
  existen, su estado, metadatos — no la conversión inmediata de esos
  hashes en URLs de certificado utilizables. Para cualquier token no
  filtrado, 2²⁵⁶ sigue siendo inviable de fuerza bruta tenga o no tenga
  el hash una clave de servidor. El pepper protege principalmente
  contra ataques de diccionario offline sobre secretos de baja entropía,
  que no es el vector relevante aquí dado que el token ya es CSPRNG de
  256 bits.
- Se usan librerías criptográficas estándar y auditadas del lenguaje/
  runtime (ej. el módulo `hashlib`/`secrets` de Python), nunca una
  implementación propia de SHA-256.

Si en el futuro se decide introducir un hash con clave de servidor
(HMAC/pepper) — por ejemplo, si cambia el modelo de amenaza o se reduce
la entropía del token — esa transición es una **migración de seguridad
separada**, documentada y aprobada como tal (nueva sección/ADR de este
mismo documento cuando corresponda), no una extensión silenciosa de
esta sección.

### 3.2 Qué se almacena y cómo funciona la verificación

- Se almacena únicamente `token_hash` (el digest SHA-256 del token, en
  hex o base64). El token en texto plano **nunca** se persiste.
- **Flujo normal de verificación** al recibir un `token` en `POST
  /api/v1/certificates/resolve`:
  1. El backend recibe el token en texto plano.
  2. Calcula `SHA-256(token)`.
  3. Consulta la columna indexada `token_hash` buscando ese digest
     exacto — una búsqueda por igualdad sobre un índice único
     (`DATA_MODEL.md` §6), no un recorrido de filas ni una comparación
     manual contra cada `token_hash` almacenado.
  4. Confirma que el registro encontrado tiene `certificate.status =
     active`.
- La resolución exitosa (`authenticity.status = authentic`) requiere
  **ambas** condiciones:
  1. `token_hash` calculado coincide con un registro existente
     (paso 3, vía lookup indexado);
  2. ese registro tiene `certificate.status = active` (paso 4).
  Un hash coincidente cuyo certificado está `revoked` o `draft` **no**
  produce `authentic` (ver también sección 14).
- **Comparación en tiempo constante — alcance correcto:** el lookup por
  igualdad de un valor indexado en la base de datos (paso 3) es una
  operación estándar de base de datos y no requiere una función de
  comparación de tiempo constante para funcionar como mecanismo de
  búsqueda. Una función como `hmac.compare_digest` en Python (válida
  para comparar cualquier par de bytes, no solo HMACs) es aplicable
  **si** código de aplicación realiza una comparación directa
  secreto-contra-secreto o digest-contra-digest fuera del motor de base
  de datos (ej. comparar dos valores ya obtenidos en memoria); no es el
  mecanismo del lookup SQL indexado en sí, que ya es eficiente por
  construcción (`DATA_MODEL.md` §6) y no requiere recalcular hashes de
  todos los certificados existentes.
- **Registro/logging:** nunca se registra el token en texto plano; el
  propio `token_hash` tampoco se registra salvo necesidad puntual de
  investigación de incidente (sección 12.2) — no forma parte del log
  rutinario de cada resolución.

No existe pepper en el MVP: no hay clave de servidor que gestionar ni
rotar para el hashing de tokens, por lo que no hay una sección de
"rotación de pepper" en este documento. La sección 20 conserva
únicamente el punto de reemisión de certificados como cambio cruzado
pendiente (no relacionado con el algoritmo de hash).

## 4. Resolución de certificado — anti-enumeración

Debe permanecer compatible con `API_CONTRACT.md` §7 tal como está
aprobado:

```text
POST /api/v1/certificates/resolve
```

Estados públicos: **únicamente** `authentic` y `unavailable`. Este
documento no reabre esa decisión — la implementa.

### 4.1 Requisitos de implementación (delegados aquí por `API_CONTRACT.md`)

El cliente público **nunca** debe poder distinguir, a partir de la
respuesta, si un token `unavailable` es:

- inexistente;
- malformado (no cumple el formato esperado);
- de un certificado revocado;
- expirado (si en el futuro existe expiración — no existe en el MVP,
  `DATA_MODEL.md` no define `expires_at`);
- inválido por cualquier otro motivo.

Requisitos concretos:

- **Respuesta genérica:** siempre `{ "authenticity": { "status":
  "unavailable" } }`, sin campos adicionales, para los cinco casos
  anteriores (ya fijado en `API_CONTRACT.md` §7; aquí se reitera como
  requisito de implementación).
- **Sin stack traces** en ninguna respuesta de este endpoint, ni
  siquiera en caso de error interno inesperado (ver forma de error
  estándar en `API_CONTRACT.md` §10).
- **Sin IDs internos**: ni `certificate.id`, ni `piece.id`, ni
  `artisan.id` en ninguna rama de la respuesta.
- **Sin `token_hash`**, completo ni parcial, en ningún caso.
- **Sin UID de NFC**, bajo ninguna circunstancia.
- **Sin revelar el motivo** del `unavailable` en el cuerpo de la
  respuesta.
- **Normalización de tiempo — mejor esfuerzo razonable (aprobado):**
  malformado, inexistente, revocado y cualquier otro token inválido
  producen siempre el mismo `authenticity.status = unavailable`, sin
  revelar el motivo. Cuando sea práctico, se evitan caminos de código
  obviamente distintos entre casos: un token **sintácticamente
  plausible** (longitud/alfabeto correctos) recorre el trabajo normal
  de hashing y lookup en base de datos aunque no exista, en vez de
  responder antes de tocar la base de datos. Esto es una normalización
  de mejor esfuerzo, no una garantía matemática: este documento **no
  promete tiempos de respuesta idénticos** entre todos los casos, y
  **no se agregan sleeps/delays artificiales fijos** para forzar
  igualdad de tiempos — dado el tamaño del espacio de tokens (2²⁵⁶),
  el beneficio marginal de una normalización perfecta de timing no
  justifica la complejidad ni la latencia añadida a un flujo de
  escaneo NFC legítimo. Un token que ni siquiera es sintácticamente
  plausible (ej. longitud claramente incorrecta) puede rechazarse
  antes de tocar la base de datos por eficiencia, sin que esto se
  considere una filtración de información práctica dado el volumen del
  piloto. El detalle de implementación exacto puede probarse/ajustarse
  más adelante sin que esto sea una decisión abierta de producto.

### 4.2 Qué sí puede registrarse internamente

Los logs internos (no la respuesta pública) **pueden** distinguir el
motivo real (inexistente / malformado / revocado) para fines de
auditoría y detección de abuso — ver sección 14 sobre qué va en
`AUDIT_EVENT` y qué no.

## 5. Rate limiting y abuso

Objetivo: mitigar fuerza bruta/enumeración contra
`certificates/resolve` y abuso de scraping contra endpoints públicos,
de forma realista para un piloto pequeño (2 artesanos, 4 piezas), sin
sobre-ingeniería.

### 5.1 `POST /api/v1/certificates/resolve`

**Valores por defecto aprobados para el MVP** (enforcement en Nginx,
ver sección 5.5):

- **30 solicitudes por minuto por IP.**
- Se permite un **burst pequeño** (unas pocas solicitudes casi
  inmediatas) antes de aplicar el límite sostenido, para no penalizar
  recargas accidentales de página ni escaneos NFC repetidos legítimos.
- El abuso repetido (superar el límite de forma sostenida) puede
  activar un **enfriamiento (cooldown) temporal** adicional para ese
  origen, más allá del `429` puntual. **No implementado en el MVP**
  (Sprint 4): queda como endurecimiento futuro; por ahora solo aplica
  el `429` puntual.
- Al superar el límite, se responde `429` (ya definido en
  `API_CONTRACT.md` §10).
- **No se introduce CAPTCHA** en el MVP: no está justificado para un
  flujo de un solo campo (`token`) consumido típicamente por un
  navegador móvil tras escanear NFC, y añadiría fricción a la
  experiencia principal del producto sin beneficio proporcional dado
  que la entropía del token ya hace la fuerza bruta inviable
  (sección 2.1). Se reevaluaría solo si se observa abuso real.

Estos valores son **defaults operativos**, configurables sin necesidad
de cambiar el contrato público de la API (`API_CONTRACT.md` no define
límites numéricos, solo que existe rate limiting y el código `429`).
Un escaneo NFC legítimo normal (una persona consultando su certificado
ocasionalmente) queda muy por debajo de 30 solicitudes por minuto, por
lo que no debería verse afectado bajo uso normal. No se sobre-diseña
rate limiting distribuido (ej. coordinación entre múltiples nodos) para
este piloto de tamaño pequeño; un límite por IP a nivel de Nginx/proceso
único es suficiente para el volumen esperado.

### 5.2 Endpoints públicos `GET` (`artisans`, `pieces`)

**Aprobado: 120 solicitudes por minuto por IP.** Más permisivo que
`certificates/resolve` (son lecturas de catálogo público, no un flujo
de credenciales), pero presente para contener scraping agresivo o bots
mal configurados. No requieren distinguir "malformado" de "no
encontrado" con el mismo rigor anti-enumeración que el certificado —
son datos ya públicos por diseño.

### 5.3 Futuro login administrativo

**Aprobado (aplicable cuando exista el endpoint):** máximo **5 intentos
de autenticación fallidos por 15 minutos**, por IP y/o por cuenta según
lo que sea aplicable al mecanismo elegido (sección 9). El mecanismo de
autenticación en sí permanece como trabajo futuro (sección 9); esta
cifra es el límite que debe respetar cualquier mecanismo que se elija,
no una decisión abierta.

### 5.4 Intentos repetidos de token inválido

- Un mismo origen (IP) generando muchos `unavailable` consecutivos
  contra `certificates/resolve` es una señal de abuso, auditable
  (sección 14) independientemente del rate limiting — el rate limiting
  contiene el volumen, el audit log permite investigar el patrón.
- Registrar estos intentos repetidos está permitido y es deseable
  (IP, timestamp, conteo) **sin registrar nunca el token en texto
  plano** de los intentos individuales (sección 3.2, sección 12.2).
- **Estado en el MVP (Sprint 4):** `AUDIT_EVENT` sigue diferido (no hay
  modelo, servicio ni API). La evidencia de abuso proviene por ahora de
  los logs operativos de Nginx (IP, timestamp, método, path, status y
  las líneas `limiting requests` del error log, con la retención de
  la sección 12.3). El registro persistente de abuso en `AUDIT_EVENT`
  es trabajo futuro.

### 5.5 Capa de enforcement e IP real del cliente

- **Nginx es la capa de enforcement del rate limiting en el MVP**; la
  configuración de ejemplo está en
  `backend/nginx/artesanfc-api.conf.example`. FastAPI no implementa un
  limitador propio: un contador en memoria no sería correcto con varios
  workers/instancias, y `request.client` puede ser el proxy y no el
  cliente.
- `certificates/resolve`: `30r/m` por IP con `burst` pequeño;
  endpoints públicos `GET`: `120r/m` por IP (zona separada). Ambos
  responden `429` con el envelope `rate_limited` y `Retry-After`, sin
  revelar nada sobre el token. Las respuestas `429` generadas por Nginx
  no llevan cabeceras CORS: el navegador las ve como fallo de
  red/servicio, y el frontend actual las trata como un fallo temporal
  (aceptable en el MVP).
- El ejemplo limita el cuerpo de `certificates/resolve` a `1k`
  (`client_max_body_size`) y añade `Cache-Control: no-store`
  (sección 7), que la API también envía por sí misma.
- **IP real del cliente:** por defecto se usa la dirección del peer TCP
  (`$remote_addr`); nunca se confía en un `X-Forwarded-For` arbitrario.
  La recuperación de IP real vía Cloudflare (`CF-Connecting-IP` +
  `set_real_ip_from`) está en el ejemplo como sección opcional
  **desactivada**, porque la topología de producción (Cloudflare
  proxied o DNS-only) aún no está confirmada. Solo debe activarse si el
  origen está realmente protegido detrás de Cloudflare (acepta solo sus
  rangos), con los rangos oficiales de Cloudflare mantenidos al día;
  de lo contrario un cliente podría falsificar la cabecera.
- Los límites son **por nodo Nginx**: con varios nodos independientes
  habría que centralizarlos o moverlos al edge (fuera del alcance del
  piloto, sección 5.1). Que la configuración exista no prueba su
  enforcement: debe verificarse en el despliegue real.

## 6. Seguridad NFC

Hardware actual (`ARCHITECTURE.md` §14, `DATA_MODEL.md` §2.4):

```text
NTAG213
13.56 MHz
ISO 14443A
```

Declaraciones explícitas (para no sobre-prometer capacidades del
hardware):

- **El UID del NFC NO es un secreto.** Es legible por cualquier lector
  NFC compatible sin autenticación (ADR-008).
- **El UID del NFC NO es un factor de autenticación** del sistema. No
  se usa, ni parcial ni derivadamente, para validar un certificado.
- **La URL/token es la credencial relevante** para la resolución del
  certificado — el sistema no depende del UID para nada relacionado
  con seguridad.
- **NTAG213 no ofrece anti-clonación criptográfica.** Es memoria NFC
  estándar; este documento **no afirma** que el hardware impida
  copiar/clonar su contenido. Un atacante con acceso físico al tag
  puede leer la URL almacenada y escribirla en otro tag NTAG213 (o
  cualquier tag compatible), produciendo un clon funcionalmente
  indistinguible para el sistema (que solo ve la URL/token). Esto es
  coherente con la sección 1.3: la seguridad no depende de la
  inclonabilidad física del tag.
- **No bloquear (`lock`) un tag hasta validar la URL definitiva**
  (ADR-021, ya aprobado): el bloqueo de escritura es irreversible en
  NTAG213 y debe aplicarse solo tras confirmar que la URL grabada es la
  correcta y funcional.
- **Una vez verificado un tag de producción, el bloqueo de escritura
  (write-protection) puede usarse** para prevenir reescritura
  accidental (por el propio equipo de ArtesaNFC, no por terceros) —
  pero el bloqueo **no** previene que un tercero con su propio
  hardware clone el contenido leyéndolo antes o después del bloqueo; el
  bloqueo protege contra reescritura accidental, no contra clonación.
- **Tags reemplazados, perdidos o dañados** se gestionan mediante el
  historial de `NFC_TAG` (`DATA_MODEL.md` §2.4: estados `replaced`/
  `retired`, historial preservado) y la política de revocación/reemisión
  de certificado (sección 15) — no se diseña aquí un mecanismo nuevo,
  se usa el ya aprobado en `DATA_MODEL.md`.

## 7. Manejo de la URL del token

Ruta conceptual ya aprobada (`PROJECT.md` §8, `ARCHITECTURE.md` §6):

```text
https://artesanfc.com/c/{token}
```

(o una ruta de resolución privada equivalente en el frontend que
internamente llame a `POST /api/v1/certificates/resolve` — el diseño
exacto de esa página es responsabilidad de frontend/ChatGPT; este
documento solo fija los requisitos de seguridad que esa página debe
cumplir, sin rediseñar su arquitectura).

Requisitos:

- El token **no aparece en ninguna navegación pública** (ADR-006): sin
  enlaces desde menús, galerías, directorios ni páginas de pieza.
- **No se incluye en el sitemap.**
- La página de resolución usa **`noindex`** (meta robots o cabecera
  `X-Robots-Tag`).
- **No se envía el token en payloads de analítica/eventos** (ej. no
  incluir la URL completa o el token como propiedad de un evento de
  analytics de terceros); si se mide el uso de esta página, se hace
  sin el valor del token en el payload.
- **Evitar loguear el token completo** en logs de reverse proxy o de
  aplicación (ver sección 14.3 sobre redacción de tokens en logs).
- **Evitar que el token llegue a URLs de terceros** por fuga de
  referrer: si la página del certificado carga cualquier recurso de
  terceros (fuentes, analítica, CDN de terceros), debe usarse
  `Referrer-Policy: strict-origin-when-cross-origin` o más estricta
  (`no-referrer` para la página de certificado específicamente es
  aceptable y recomendado, dado que la URL completa contiene el
  token).
- **HTTPS obligatorio** en toda la cadena (usuario → frontend → API),
  sin excepción, para que el token nunca viaje en texto plano por red.
- **`Cache-Control: no-store` (aprobado, requisito de MVP):** tanto la
  página `/c/{token}` (donde sea práctico controlarlo desde el
  frontend/edge) como las respuestas de `POST
  /api/v1/certificates/resolve` deben servirse con `Cache-Control:
  no-store`. El contenido del certificado (público o privado) no debe
  almacenarse intencionalmente en cachés compartidas (proxies, CDN
  compartido, cachés intermedias) ni en la caché HTTP normal del
  navegador. **Aclaración de alcance:** esto previene el
  almacenamiento en caché HTTP del contenido/respuesta; **no** elimina
  la URL del historial de navegación del navegador (`browser history`),
  que es un mecanismo distinto no afectado por `Cache-Control`. Este
  requisito **no modifica** `API_CONTRACT.md`: es un header de
  seguridad a nivel de transporte/implementación, no un cambio a la
  forma del cuerpo JSON del contrato (`API_CONTRACT.md` §16 ya delega a
  este documento la semántica de transporte de `certificates/resolve`).
  **Implementación (MVP):** la API lo añade a todas las respuestas de
  esa ruta (200, 422, 405) mediante un middleware acotado a ese path —
  no a los `GET` públicos — y el ejemplo de Nginx lo repite.

`CROSS-DOCUMENT CHANGE REQUIRED`: ninguno identificado — estos
requisitos son compatibles con `API_CONTRACT.md` tal como está (el
contrato ya no expone el token en ninguna respuesta, la página
`/c/{token}` es responsabilidad de frontend consumiendo
`certificates/resolve`, y el header `Cache-Control: no-store` es un
detalle de transporte que no altera el cuerpo JSON documentado en
`API_CONTRACT.md`).

## 8. Gestión de secretos

Ya aprobado en `ARCHITECTURE.md` §12, reiterado y extendido aquí con
alcance de seguridad. **Aprobado como enfoque del MVP** (no queda
ningún punto abierto en esta sección):

- **Los secretos reales permanecen fuera de Git**, siempre.
- **Nunca se versiona** en el repositorio: `.env`, contraseñas, claves
  privadas, tokens administrativos, ni ningún secreto de aplicación.
- `.env` debe estar en `.gitignore`.
- `.env.example` contiene **solo nombres de variables y placeholders**,
  nunca valores reales.
- **Los secretos de producción se inyectan mediante variables de
  entorno del servidor/despliegue** (ej. variables de entorno del
  contenedor o del proceso, configuradas fuera del repositorio).
- Si se usan **archivos de secretos** (en vez de variables de entorno
  puras), esos archivos viven **fuera del repositorio** y con permisos
  de filesystem restrictivos (ej. legibles solo por el usuario que
  ejecuta la aplicación).
- **No se requiere una plataforma dedicada de gestión de secretos para
  el MVP** (ej. Vault, AWS Secrets Manager); variables de
  entorno/archivos fuera del repositorio con permisos restrictivos son
  suficientes para el volumen y superficie actual. Un gestor de
  secretos dedicado puede adoptarse más adelante si la infraestructura
  crece — no es una decisión abierta, es una mejora futura razonable a
  reevaluar cuando aplique.
- **Rotación:** cualquier secreto expuesto (commit accidental, log,
  servidor comprometido) se rota inmediatamente, incluso si el commit
  se elimina del historial (ver sección 19, respuesta a incidentes).
- Alcance de secretos a proteger: contraseña de base de datos,
  cualquier clave de firma administrativa futura, credenciales de
  Cloudflare (API tokens si se usan para despliegue automatizado de
  Pages), credenciales SMTP u otros servicios de terceros que se
  integren a futuro. (No hay pepper de hashing de certificados que
  proteger en el MVP — sección 3.1.)

## 9. Seguridad administrativa (mínima, sin sobre-diseñar)

`API_CONTRACT.md` §14 deja explícitamente sin decidir el mecanismo de
autenticación administrativa. Este documento define **requisitos
mínimos**, no el mecanismo:

- Todo endpoint bajo `/api/v1/admin/...` **requiere autenticación**;
  ninguno es accesible sin credenciales válidas, sin excepción.
- **Principio de mínimo privilegio**: en el MVP con un operador (o muy
  pocos), esto puede ser tan simple como una única cuenta
  administrativa con acceso completo, pero el diseño no debe asumir
  que nunca habrá más de un rol — ver "trabajo futuro" abajo.
- **Sin contraseñas por defecto compartidas.** Ninguna credencial de
  fábrica, de ejemplo, ni reutilizada entre entornos (local/staging/
  producción).
- **Autenticación fuerte**: contraseña con requisitos mínimos de
  longitud/complejidad razonables si se usa contraseña, o un mecanismo
  equivalente si se usa otro esquema (ej. claves de API rotables).
  Hashing de contraseña administrativa (si existe) **sí** debe usar
  Argon2 o bcrypt — a diferencia de los tokens de certificado (sección
  3), una contraseña elegida por un humano puede tener entropía baja,
  por lo que aquí el hashing lento **sí** está justificado.
- **Manejo seguro de sesión/token**: cookies de sesión con `HttpOnly`,
  `Secure` y `SameSite` apropiados si se usa sesión basada en cookie;
  o tokens con expiración razonable si se usa un esquema tipo JWT/API
  key. El mecanismo exacto es una decisión pendiente (abajo).
- **Rate limiting** específico sobre el endpoint de login/autenticación
  administrativa (sección 5.3), más estricto que los límites públicos.
- **Audit logging** de todo acceso y cambio administrativo (sección
  14).
- **HTTPS obligatorio** para toda la superficie administrativa, sin
  excepción.
- **Consideraciones CSRF** si en el futuro se adopta autenticación
  basada en cookies: se requeriría un token CSRF o el uso de
  `SameSite=Strict`/`Lax` como mitigación; no aplica si el mecanismo
  final es un token portado en header (`Authorization: Bearer ...`),
  que no es vulnerable a CSRF de la misma forma.
- **CORS** restrictivo específico para el namespace admin (sección
  10).

**Trabajo futuro explícitamente diferido** (no es un `PROPOSED
DECISION` abierto para el MVP público actual — es alcance
deliberadamente fuera de esta revisión, a resolver cuando se diseñe la
API administrativa):

- Mecanismo exacto de autenticación: sesión con cookie vs. JWT vs. API
  key vs. otro. Ninguno se elige en este documento, y no se elige por
  descarte tampoco — queda abierto intencionalmente.
- Transporte del credential (cookie vs. header `Authorization`).
- Roles/niveles de autorización (¿un solo rol "admin" es suficiente
  para el MVP con 2 artesanos, o se necesita un rol de solo lectura
  desde ya?). Se recomienda **un solo rol admin para el MVP**, dado el
  volumen actual, aunque `DATA_MODEL.md` §2.6 ya previó
  `actor_type`/`actor_id` genérico pensando en más de un actor a
  futuro.
- Política de expiración/renovación de sesión o token.
- Requisitos exactos de complejidad de contraseña, si se usa
  contraseña.

Estos puntos son trabajo futuro de diseño de backend/admin, coordinado
cuando se priorice esa fase — no bloquean el resto de este documento ni
el MVP público, y no requieren decisión de Alexis ahora mismo.

## 10. CORS

**Política de producción (aprobada por este documento, dentro del
alcance de seguridad que le corresponde):**

- La API pública (`/api/v1/artisans`, `/api/v1/pieces`,
  `/api/v1/certificates/resolve`) restringe `Access-Control-Allow-Origin`
  a los dominios de frontend controlados por ArtesaNFC:
  `https://artesanfc.com` (y su variante `www` si se usa, y el dominio
  de staging correspondiente). **No se usa `Access-Control-Allow-Origin:
  *`** ni siquiera para la API pública.
- **Alcance real de CORS (aclaración importante):** CORS es un mecanismo
  aplicado por navegadores, no por el servidor frente a cualquier
  cliente. Restringir `Access-Control-Allow-Origin` **no** impide ni
  mitiga bots, `curl`, scripts server-to-server, clientes HTTP
  personalizados, ni scraping automatizado en general — ninguno de esos
  clientes está sujeto a la política CORS de un navegador. El propósito
  real de esta restricción es limitar **qué orígenes de navegador
  pueden leer la respuesta** de una solicitud cross-origin hecha desde
  una página web, reduciendo la exposición innecesaria de la API a
  páginas de terceros que un usuario pueda visitar. La defensa real
  contra fuerza bruta/scraping/abuso es el **rate limiting** (sección
  5), no CORS; esta sección no cambia ninguna cifra de rate limiting.
- La API administrativa (`/api/v1/admin/...`), cuando exista, usa una
  política aún más restrictiva: únicamente el origen del panel
  administrativo (que puede no ser el mismo dominio que el sitio
  público), y **nunca** `*`.
- Métodos y headers permitidos se limitan a los efectivamente usados
  por cada superficie (no se habilita `*` en `Access-Control-Allow-
  Methods`/`Headers` por defecto).

**Trabajo futuro explícitamente diferido:** si el panel administrativo
será un subdominio dedicado (ej. `admin.artesanfc.com`), una ruta del
mismo frontend (`artesanfc.com/admin`), o una aplicación separada, no
se define ni se congela en este documento ni en `ARCHITECTURE.md`/
`API_CONTRACT.md`. Este documento solo exige que, sea cual sea el
origen final elegido, **se declare explícitamente en la allowlist de
CORS de producción** — nunca `*`, y nunca inferido implícitamente.

## 11. Headers / seguridad de transporte

Requisitos esperados en producción (implementación coordinada entre
Nginx y Cloudflare Pages/frontend, según `ARCHITECTURE.md` §3, sin
fijar aquí la configuración exacta):

- **HTTPS obligatorio** en todo el sistema; sin variante HTTP servida
  en producción (redirección forzosa a HTTPS como mínimo).
- **HSTS**, activado **después** de confirmar que HTTPS funciona de
  forma estable en todos los subdominios relevantes (`artesanfc.com`,
  `api.artesanfc.com`), para evitar bloquear accidentalmente el sitio
  si hay un problema de certificado — se recomienda activarlo con un
  `max-age` corto inicialmente y aumentarlo una vez confirmado.
- **`Content-Security-Policy`**: al menos una política base que
  restrinja `script-src`/`style-src` a orígenes conocidos y bloquee
  `object-src`; el detalle fino depende de qué recursos de terceros use
  el frontend (fuentes, modelos 3D, video) — se coordina con
  `DESIGN_SYSTEM.md`/frontend sin definirse en detalle aquí.
- **`X-Content-Type-Options: nosniff`** en todas las respuestas.
- **`Referrer-Policy`**: `strict-origin-when-cross-origin` como default
  del sitio; `no-referrer` recomendado específicamente para la página
  de certificado (sección 7).
- **`Permissions-Policy`**: deshabilitar APIs del navegador no usadas
  por el sitio (ej. cámara, micrófono, geolocalización) salvo que una
  función futura (ej. AR para 3D) las requiera explícitamente.
- **Protección contra clickjacking**: `frame-ancestors 'none'` (vía
  CSP) o `X-Frame-Options: DENY`, salvo que exista una razón legítima
  documentada para permitir embeber alguna página (no se conoce
  ninguna en el MVP).

`CROSS-DOCUMENT CHANGE REQUIRED`: ninguno — estos headers son
responsabilidad de configuración de Nginx/Cloudflare Pages, no
requieren cambios a `DATA_MODEL.md` ni `API_CONTRACT.md`.

## 12. Audit logging

`DATA_MODEL.md` §2.6 define `AUDIT_EVENT` como append-only/inmutable.
Este documento define **qué eventos** deben auditarse y qué no debe
registrarse nunca.

### 12.1 Eventos que deben auditarse (mínimo)

- Login administrativo exitoso y fallido.
- Creación/edición de artesano.
- Creación/edición de pieza.
- Cambios de `publication_status` (artesano o pieza).
- Emisión de certificado (`certificate` pasa de `draft` a `active`).
- Revocación de certificado, incluyendo el motivo interno (sección 15).
- Asignación, reemplazo y bloqueo (`locked`) de un `NFC_TAG`.
- Abuso detectado contra `certificates/resolve` (ej. una IP superando
  el rate limit repetidamente, o un volumen alto de `unavailable`
  consecutivos desde el mismo origen).
- Cambios de configuración relevantes a seguridad (ej. cambio de
  política de rate limiting, rotación de secretos administrativos
  futuros), cuando se realicen a través del propio sistema
  administrativo — cambios hechos
  directamente en el servidor (fuera de la aplicación) quedan fuera
  del alcance de `AUDIT_EVENT` y dependen de logs de sistema/acceso
  SSH (sección 17).

Cada evento usa `action` con namespace ya aprobado en `DATA_MODEL.md`
(ej. `certificate.issued`, `certificate.revoked`, `nfc_tag.locked`,
`admin.login_failed`, `piece.published`).

### 12.2 Qué NO debe registrarse nunca

- El token privado del certificado en texto plano, en ningún campo de
  `AUDIT_EVENT.metadata` ni en ningún log de aplicación.
- Contraseñas, en texto plano o con cualquier hashing reversible.
- Secretos de sesión (cookies de sesión, JWT completos) — si se
  necesita referenciar una sesión en un log, se usa un identificador
  no reutilizable como secreto (ej. un ID de sesión opaco, no el token
  de sesión en sí).
- `token_hash`, salvo que exista una razón de investigación de
  incidente específica y documentada — por defecto no se incluye en
  `AUDIT_EVENT.metadata` porque no aporta valor de auditoría rutinaria
  y amplía la superficie de un hipotético log filtrado.
- Credenciales sensibles completas de cualquier tipo (claves de API de
  terceros, contraseñas de base de datos).

### 12.3 Direcciones IP

`DATA_MODEL.md` §12 ya marca `audit_event.ip_address` como dato
personal cuya retención/anonimización se define aquí:

- Se registra la IP en eventos de seguridad relevantes (login
  administrativo, abuso contra `certificates/resolve`) porque es
  necesaria para investigar y mitigar abuso.
- **Política aprobada de retención (MVP):** las direcciones IP en
  texto claro dentro de logs de seguridad/aplicación se retienen como
  máximo **30 días**, salvo que un incidente activo requiera
  preservarlas temporalmente más allá de ese plazo mientras dure la
  investigación. Pasado ese plazo (o resuelto el incidente), se
  eliminan o se anonimizan.
- Los registros de auditoría que **no** requieren la IP en claro para
  cumplir su propósito (ej. el hecho de que se emitió/revocó un
  certificado) pueden conservarse por más tiempo sin la IP asociada,
  ya que el valor de auditoría a largo plazo no depende de ese dato.
- **No se recolecta IP ni información personal sin una necesidad
  operativa o de seguridad concreta** — no se agrega captura de IP a
  eventos que no la necesitan solo "por si acaso".
- Esta es una **política operativa de privacidad para el MVP**, no una
  declaración de cumplimiento legal (ver sección 13).

## 13. Logging y privacidad (reglas prácticas)

- **No registrar la URL completa del token de certificado** en logs de
  aplicación o de acceso; si se necesita loguear que hubo una solicitud
  a `certificates/resolve`, se registra el hecho y el resultado
  (`authentic`/`unavailable`), no el token.
- **Redactar tokens** si aparecen incidentalmente en cualquier log
  (ej. logs de acceso de Nginx que capturan la ruta completa
  `/c/{token}` si esa ruta llega a tocar el servidor en vez de
  resolverse client-side): se recomienda configurar el logging de
  acceso para excluir o enmascarar el segmento de path que contiene el
  token.
- **Errores de la base de datos en el ciclo de vida.** El `DETAIL` de
  PostgreSQL incluye valores de la fila (para `certificate`, el
  `token_hash`). Los servicios de ciclo de vida traducen los fallos de
  restricción a errores de dominio (`LifecycleError` y derivados) que
  contienen como máximo el nombre de la restricción y el SQLSTATE, sin
  `DETAIL`, sin valores de fila y **sin encadenar** la excepción original
  (`__cause__`/`__context__` son `None`). **Limitación conocida:** esto
  cubre solo los errores originados en esos servicios; un error de base
  de datos no controlado en cualquier otra ruta (por ejemplo el `commit`
  del llamador) aún llega al log del proceso vía el 500 global, y su
  saneamiento queda como tarea aparte.
- **Evitar información personal innecesaria** en logs de aplicación más
  allá de lo estrictamente necesario para seguridad/depuración (sección
  12.3 sobre IPs).
- **Retención aprobada**: máximo 30 días para IPs en claro en logs de
  seguridad/aplicación, salvo incidente activo en curso (mismo criterio
  que sección 12.3); registros que no necesiten la IP pueden retenerse
  más tiempo sin ella.
- **Separación de logs de seguridad**: cuando sea práctico, mantener
  los logs de eventos de seguridad (audit log, intentos de login,
  abuso) accesibles/revisables independientemente de logs operativos
  generales, para facilitar la investigación de incidentes sin tener
  que filtrar ruido.
- **Acceso a logs restringido**: solo el equipo con responsabilidad de
  backend/infraestructura (Claude, según `PROJECT.md` §12) y Alexis
  como Product Owner deberían poder acceder a logs que contengan IPs o
  detalles de intentos administrativos; no se documenta aquí un
  mecanismo técnico de control de acceso a logs, solo el principio.

No se afirma cumplimiento de ningún marco legal específico (ej. GDPR,
LFPDPPP) en este documento — eso requeriría asesoría legal explícita,
fuera del alcance de este documento técnico.

## 14. Revocación de certificados

Comportamiento ya compatible con `DATA_MODEL.md` §2.3 y
`API_CONTRACT.md` §7:

- Un certificado revocado (`certificate.status = revoked`) resuelve
  públicamente como `authenticity.status = unavailable`, indistinguible
  de un token inexistente (sección 4).
- El motivo interno de revocación (`certificate.revocation_reason`,
  campo ya existente en `DATA_MODEL.md`, opcional) puede almacenarse
  para uso administrativo/auditoría, pero **nunca** se expone
  públicamente.
- La revocación se audita (`certificate.revoked` en `AUDIT_EVENT`,
  sección 12.1), incluyendo quién la ejecutó (`actor_id` cuando exista
  modelo de usuario administrativo) y cuándo.
- Tras la revocación, el token anterior **no debe volver a validar**:
  dado que `token_hash` no se borra (`DATA_MODEL.md` §2.3), la
  verificación debe comprobar tanto la coincidencia del hash **como**
  `status = active` antes de responder `authentic` — un certificado
  `revoked` con `token_hash` coincidente debe responder `unavailable`,
  no `authentic`. Este es un requisito de lógica de verificación, no
  solo de almacenamiento.

### 14.1 Reemplazo/reemisión (conceptual, sin transferencia de propiedad)

Sin diseñar un sistema de propiedad (ADR-010, `DATA_MODEL.md` §3, fuera
de alcance de este documento):

- Si un NFC se pierde, se daña o su token se sospecha comprometido, el
  flujo conceptual deseado es: **revocar el certificado activo** →
  **emitir un certificado de reemplazo** para la misma pieza, con un
  token completamente nuevo generado de forma independiente (sección
  2) → **el token anterior nunca vuelve a ser válido** (ya garantizado
  por la lógica de verificación de la sección 14, que exige `status =
  active`) → **programar un nuevo `NFC_TAG`** en estado
  `available`/`programmed` para esa pieza, marcando el `NFC_TAG`
  anterior como `replaced`/`retired` (`DATA_MODEL.md` §2.4, ya
  soportado por el historial preservado).
- El historial de NFC (`DATA_MODEL.md` §2.4) ya soporta este patrón sin
  cambios. **El historial de certificados no lo soporta todavía** — ver
  el `CROSS-DOCUMENT CHANGE REQUIRED` abajo, que es la única limitación
  cruzada que queda en este documento.

`CROSS-DOCUMENT CHANGE REQUIRED` (el único de este documento): el flujo
de reemisión descrito arriba **no es implementable** bajo la
restricción actual de `DATA_MODEL.md` §6, donde `certificate.piece_id`
es unique de forma global (0..1 certificado **en total**, no 0..1
**activo**). Esto impide preservar un certificado `revoked` histórico
y crear uno `active` nuevo para la misma pieza al mismo tiempo.

Seguridad requiere poder revocar un token comprometido y emitir un
reemplazo **sin destruir el historial**. El cambio futuro necesario en
`DATA_MODEL.md` (no realizado en esta tarea) sería, conceptualmente:

- Una pieza puede tener **múltiples registros históricos** de
  `CERTIFICATE`.
- **Como máximo uno** de esos registros puede tener `status = active`
  para una pieza dada, en cualquier momento.
- Los certificados revocados **permanecen preservados** (no se borran
  ni se sobrescriben).
- Un certificado de reemplazo recibe un **token completamente nuevo e
  independiente** (nunca derivado del anterior).
- El token antiguo **nunca vuelve a ser válido**, incluso después de
  emitido el reemplazo.
- En términos de esquema, esto reemplazaría la actual
  `UNIQUE(piece_id)` global por una restricción de unicidad **parcial**
  sobre los certificados `active` (el mismo patrón ya usado para
  `nfc_tag` en `DATA_MODEL.md` §4, restricción B: "como máximo un tag
  **activo** por pieza, histórico preservado").

Esta sección **no modifica `DATA_MODEL.md`**; documenta el requisito de
seguridad y deja explícito que su resolución pertenece a una revisión
futura de ese documento (issue/ADR separado), coordinada con Alexis y
backend antes de implementarse.

## 15. Backups y seguridad de base de datos

Requisitos mínimos para el MVP:

- **PostgreSQL no expuesto públicamente** más allá de lo necesario: el
  puerto de base de datos no debe ser accesible desde internet; solo
  el backend (en la misma red/host o red privada) se conecta
  directamente.
- **Credenciales de base de datos fuertes**, generadas aleatoriamente,
  no reutilizadas entre entornos.
- **Usuario de base de datos de mínimo privilegio** para la aplicación
  (permisos limitados a las operaciones que FastAPI realmente necesita
  sobre las tablas de `DATA_MODEL.md`; no usar el superusuario de
  PostgreSQL para la aplicación).
- **Transporte cifrado** entre backend y base de datos cuando ambos no
  estén en el mismo host/red de confianza (ej. `sslmode=require` si
  PostgreSQL está en un servicio gestionado separado).
- **Backups regulares**, con frecuencia acorde al volumen de cambios
  esperado (bajo, dado el tamaño del catálogo — pero los backups
  siguen siendo necesarios porque el registro es la única fuente de
  verdad, `ARCHITECTURE.md` §3).
- **Acceso a backups restringido**: los archivos de backup contienen
  `token_hash` y datos personales potenciales (`languages`,
  `public_contact`); deben protegerse con el mismo nivel de cuidado
  que la base de datos en vivo, no tratarse como archivos triviales.
- **Pruebas de restauración (aprobado):** un backup nunca verificado no
  es confiable. Se realiza una **prueba de restauración antes del
  lanzamiento a producción**, y se repite **al menos trimestralmente**
  durante el piloto. El resultado de cada prueba (éxito/falla,
  hallazgos) se documenta. La frecuencia de **creación** de backups en
  sí (a diferencia de las pruebas de restauración) se configura
  operativamente según la frecuencia real de cambio de datos, sin
  fijarse como cifra en este documento.
- **Sin secretos embebidos en scripts de backup**: credenciales de
  conexión usadas por scripts de backup se leen de variables de
  entorno/secretos (sección 8), nunca hardcodeadas en el script.

## 16. Servidor y despliegue

Requisitos base:

- **Actualizaciones del sistema operativo Linux** aplicadas
  regularmente (parches de seguridad al menos).
- **Firewall** habilitado, exponiendo únicamente los puertos
  necesarios (HTTPS/443, y SSH si se administra remotamente — ver
  abajo).
- **SSH con claves**, no contraseñas, para acceso administrativo al
  servidor.
- **Deshabilitar login SSH directo como root** donde sea práctico;
  usar un usuario con `sudo` en su lugar.
- **Nginx como reverse proxy** frente a FastAPI (`ARCHITECTURE.md`
  §3): FastAPI **no se expone directamente a internet** si Nginx está
  al frente — Nginx es el único punto de entrada HTTP/HTTPS del
  backend.
- **Certificados HTTPS** válidos y renovados automáticamente (ej.
  Let's Encrypt) para `api.artesanfc.com`.
- **Contenedores con mínimo privilegio** donde se use Docker
  (`ARCHITECTURE.md` menciona `Dockerfile`/`docker-compose.yml`): no
  ejecutar procesos de aplicación como `root` dentro del contenedor
  cuando sea evitable.
- **Actualizaciones de dependencias** del runtime (Python, PostgreSQL)
  aplicadas con una cadencia razonable, priorizando parches de
  seguridad.
- **Modo debug deshabilitado en producción**: FastAPI/Uvicorn sin modo
  de recarga automática ni páginas de error detalladas (que podrían
  filtrar stack traces) en el entorno de producción.

## 17. Dependencias / cadena de suministro

- **Fijar versiones de dependencias** donde sea práctico
  (`requirements.txt` con versiones exactas o rangos acotados, no
  `latest` implícito).
- **Revisar actualizaciones de dependencias** antes de aplicarlas en
  producción, en vez de actualizar automáticamente sin revisión.
- **Evitar paquetes innecesarios**: mantener la superficie de
  dependencias mínima para lo que el backend realmente necesita.
- **No instalar scripts/paquetes de fuentes no verificadas** en el
  entorno de producción.
- **Usar alertas de seguridad de dependencias de GitHub** (Dependabot
  o equivalente) donde esté disponible, como señal de alerta temprana,
  no como sustituto de revisión humana antes de aplicar cambios
  críticos.
- **Mantener parchado** el runtime de FastAPI, PostgreSQL y el sistema
  operativo (reitera sección 16, aplicado específicamente a
  dependencias de aplicación).

## 18. Respuesta a incidentes (mínima, MVP)

Procesos mínimos, no un plan de respuesta a incidentes completo:

### Si un token de certificado se filtra (ej. compartido públicamente por error, o sospecha de acceso no autorizado)

1. Revocar el certificado afectado (`certificate.status = revoked`).
2. Si corresponde (sospecha real de compromiso, no solo un
   compartir accidental del poseedor legítimo), generar un token de
   reemplazo — sujeto a la limitación de modelo de datos descrita en
   la sección 14.1 (`CROSS-DOCUMENT CHANGE REQUIRED` pendiente para
   soportar esto limpiamente).
3. Reprogramar el NFC de reemplazo si se emite un nuevo token.
4. Registrar el incidente en `AUDIT_EVENT` con el motivo real
   (`certificate.revoked`, con `revocation_reason` interno).

### Si un secreto de servidor se filtra (credenciales de DB, clave administrativa futura, etc.)

1. Rotar el secreto inmediatamente.
2. Invalidar credenciales/sesiones afectadas donde sea relevante. Nota:
   como el MVP no usa pepper para el hashing de tokens (sección 3.1),
   la filtración de un secreto de servidor **no** invalida por sí sola
   los certificados existentes — el `token_hash` no depende de ningún
   secreto de servidor, solo del token mismo. Un token de certificado
   filtrado se gestiona por el flujo de la subsección anterior
   (revocación), no mediante rotación de secretos de servidor.
3. Auditar y revisar logs para determinar alcance del acceso no
   autorizado.

### Si un secreto se commitea a GitHub

1. Tratarlo como **comprometido inmediatamente**, incluso si el commit
   se elimina o se hace `force-push` después — el historial de Git
   distribuido y posibles forks/clones ya pudieron haberlo capturado.
2. Rotar el secreto sin demora, sin esperar a "limpiar" el historial
   de Git primero.
3. Auditar si el secreto fue usado por terceros antes de la rotación.

## 19. No-objetivos de seguridad (qué el MVP NO garantiza)

Declaración explícita para evitar promesas implícitas:

- **No se puede garantizar que un tag NFC no sea clonado físicamente**
  por un atacante decidido con acceso al tag (sección 6, sección 1.3).
- **El sistema no prueba propiedad legal** de la pieza — certifica
  autenticidad/procedencia según lo registrado administrativamente, no
  titularidad legal (ADR-010).
- **No se provee procedencia basada en blockchain** — explícitamente
  fuera del MVP (`PROJECT.md` §4).
- **No se puede impedir que alguien fotografíe o comparta la página
  del certificado** una vez que la visualiza; el certificado no
  implementa ningún mecanismo de DRM ni de control posterior a la
  visualización.
- **No sustituye la autenticación experta de una obra física** (ej.
  peritaje de un experto en la técnica/material) — es un registro
  administrativo respaldado por el proceso de captura de ArtesaNFC,
  no una prueba forense independiente.
- **La autenticidad del certificado es tan confiable como el proceso
  de registro administrativo y la seguridad del sistema que lo
  respalda** — si un administrador registra datos incorrectos, o si el
  sistema es comprometido, el certificado refleja esa falla; el
  sistema no puede detectar por sí mismo un error de captura humana en
  el origen.

## 20. Resumen de `CROSS-DOCUMENT CHANGE REQUIRED`

**Resuelto (actualizado en el cierre de Sprint 3, 2026-09-17).** Ya no
queda ningún punto abierto (tampoco había uno relacionado con pepper,
porque el MVP no usa pepper — sección 3.1):

1. **Reemisión de certificado preservando historial** (sección 14.1) —
   **resuelto por `DATA_MODEL.md` §14.** El problema original: `DATA_MODEL.md`
   §6 definía `certificate.piece_id` como unique de forma global (0..1
   certificado **en total** por pieza), lo cual impedía preservar un
   certificado `revoked` histórico y emitir uno `active` nuevo para la
   misma pieza sin destruir el anterior, cuando la seguridad requiere
   poder revocar un token comprometido y emitir un reemplazo con un
   token completamente nuevo **sin perder el historial** del
   certificado revocado.

   `DATA_MODEL.md` §14 (2026-09-16, rama `docs/certificate-history`)
   resuelve esto: `certificate.piece_id` deja de ser unique global y
   pasa a tener un **unique index parcial** sobre `piece_id` donde
   `status = 'active'` (`DATA_MODEL.md` §2.3/§4 restricción C', §6),
   conceptualmente equivalente a `UNIQUE(piece_id) WHERE status =
   'active'`. Esto garantiza como máximo **un** certificado `active`
   por pieza en cualquier momento, mientras permite múltiples filas
   históricas (`revoked`, y cualquier `draft` histórico) para la misma
   pieza — el mismo patrón ya aprobado para `NFC_TAG`
   (`DATA_MODEL.md` §4, restricción B). Un certificado `revoked` nunca
   se borra ni se sobrescribe, y un reemplazo recibe un `token_hash`
   derivado de un token completamente nuevo e independiente
   (`DATA_MODEL.md` §2.3). Esta revisión no cambió el comportamiento
   observable de `API_CONTRACT.md` §7 (`authentic`/`unavailable` sin
   cambios).

   Nota de alcance: esta resolución es a nivel de modelo de datos
   (`DATA_MODEL.md` §14); la implementación de las tablas
   `certificate`/`nfc_tag`/`audit_event` en sí permanece fuera de
   Sprint 3 y se realiza en Sprint 4 (`WORKFLOW.md` §14), sin cambios a
   este documento.

Ningún otro requisito de este documento requiere modificar
`DATA_MODEL.md` o `API_CONTRACT.md`.

## 21. Resumen de `PROPOSED DECISION`

**No quedan `PROPOSED DECISION` abiertos en este documento.** Todos los
puntos de la revisión anterior fueron resueltos por Alexis (Product
Owner) el 2026-09-16:

1. Tamaño y codificación del token (256 bits, Base64 URL-safe) —
   aprobado como estándar del MVP (sección 2.1).
2. Algoritmo de hash del token — aprobado: `SHA-256(token)` sin
   pepper, reemplazando la recomendación anterior de HMAC/pepper
   (sección 3.1). Esto también elimina la necesidad de un esquema de
   rotación de pepper.
3. Normalización de timing en `certificates/resolve` — aprobado como
   mejor esfuerzo razonable, sin promesa de tiempos idénticos ni
   delays artificiales fijos (sección 4.1).
4. Cifras de rate limiting — aprobadas: 30 req/min/IP en
   `certificates/resolve`, 120 req/min/IP en endpoints públicos `GET`,
   5 intentos fallidos/15 min en futuro login administrativo (sección
   5).
5. Gestión de secretos — aprobado: variables de entorno/archivos fuera
   del repositorio con permisos restrictivos son suficientes para el
   MVP; no se requiere una plataforma dedicada de gestión de secretos
   por ahora (sección 8).
6. Retención de IPs y logs — aprobado: máximo 30 días en claro salvo
   incidente activo; registros que no necesiten la IP pueden retenerse
   más tiempo sin ella (secciones 12.3 y 13).
7. Pruebas de restauración de backups — aprobado: antes del
   lanzamiento y al menos trimestralmente durante el piloto, con cada
   resultado documentado; la frecuencia de creación de backups en sí
   queda como parámetro operativo (sección 15).

Dos puntos que antes figuraban aquí **no se resuelven como decisión
aprobada, sino que se reclasifican explícitamente como trabajo futuro
diferido** (no bloquean el MVP público ni requieren decisión de Alexis
ahora mismo):

- Mecanismo exacto de autenticación administrativa (sesión/JWT/API
  key), su transporte, roles y expiración (sección 9).
- Dominio/arquitectura final del panel administrativo, relevante para
  la política de CORS administrativa (sección 10) — este documento solo
  exige que, sea cual sea el origen elegido, se declare explícitamente
  en la allowlist de CORS.
