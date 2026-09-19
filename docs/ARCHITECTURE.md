# ArtesaNFC — Arquitectura objetivo

**Estado:** Propuesta aprobada para implementación incremental  
**Dominio:** `artesanfc.com`  
**Fecha de referencia:** 2026-09-16

## 1. Estado actual del repositorio

El repositorio existente nació con una arquitectura basada en sitio estático, Cloudflare Pages, Cloudflare D1 y lógica de certificados en Cloudflare. La estructura actual incluye `public/`, `src/`, `db/`, `docs/` y `wrangler.toml`.

La migración debe ser incremental. No se elimina el sistema anterior hasta que exista reemplazo funcional.

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
Nginx
  │
  ▼
FastAPI
  │
  ▼
PostgreSQL
Fuente de verdad
```

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

### Nginx

- Reverse proxy a FastAPI.
- Headers.
- Límites de tamaño.
- Rate limiting complementario.
- Separación de rutas públicas y administrativas.

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
staging
production
```

Producción prevista:

```text
artesanfc.com
api.artesanfc.com
```

### Base de la API en el frontend

La base de la API tiene una sola fuente de verdad:
`frontend/assets/js/api-config.js`. La elige por **hostname exacto** de la
página (sin comodines ni configuración por HTML):

| Hostname de la página | Base de la API |
|---|---|
| `localhost`, `127.0.0.1` | `http://127.0.0.1:8000/api/v1` |
| `artesanfc.com` | `https://api.artesanfc.com/api/v1` |
| cualquier otro (`www`, `*.pages.dev`, `file://`, `[::1]`, …) | sin resolver (`null`) |

- Con la base sin resolver, `api.js` no hace ninguna petición de red: las
  páginas públicas conservan su contenido estático y `/c/{token}` muestra su
  estado de error de servicio. Un host no-local nunca puede resolver a
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

## 16. Regla de dependencias

Frontend depende de `API_CONTRACT.md`.

Backend depende de `DATA_MODEL.md`, `SECURITY.md` y `API_CONTRACT.md`.

Ningún agente debe asumir silenciosamente estructuras no documentadas de la otra capa.
