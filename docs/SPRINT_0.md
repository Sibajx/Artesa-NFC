# Sprint 0 — Cierre

**Estado:** COMPLETED
**Fecha de referencia:** 2026-09-16
**Alcance:** fundaciones y documentación del proyecto ArtesaNFC.

Este documento cierra formalmente Sprint 0. Marca la finalización del
sprint de **fundaciones/documentación**, no del MVP completo de
ArtesaNFC. La arquitectura objetivo de frontend/backend definida
durante Sprint 0 todavía no está implementada de extremo a extremo:
ArtesaNFC conserva implementación/legado existente, que se migrará de
forma incremental (`docs/ARCHITECTURE.md` §15) en vez de descartarse
de una sola vez. Lo que Sprint 0 entrega es el conjunto de contratos
técnicos y de producto necesarios para que Sprint 1 en adelante pueda
implementarse sin que cada colaborador tenga que inventar arquitectura
por su cuenta.

## 1. Entregables completados

Todos los documentos siguientes existen en `/docs`, están aprobados y
no tienen `PROPOSED DECISION` abierto. Este documento no duplica su
contenido — solo referencia su propósito y estado.

| Documento | Propósito | Estado |
|---|---|---|
| `docs/PROJECT.md` | Carta del proyecto: propuesta de valor, alcance del MVP, actores, rutas públicas y criterios de éxito. | Aprobado |
| `docs/ARCHITECTURE.md` | Arquitectura objetivo (Cloudflare Pages + FastAPI + Nginx + PostgreSQL), estructura de repositorio y estrategia de migración. | Aprobado |
| `docs/DECISIONS.md` | Registro de ADRs (ADR-001 a ADR-025) — decisiones congeladas de producto, seguridad y visuales. | Aprobado |
| `docs/DESIGN_SYSTEM.md` | Sistema visual v1: paleta, tipografía, layout, navegación, animación, 3D/360, accesibilidad. Issue #2. | Aprobado |
| `docs/DATA_MODEL.md` | Modelo de datos del MVP: entidades, relaciones, constraints, índices. Incluye la revisión de historial de certificados (sección 14). Issue #3. | Aprobado |
| `docs/API_CONTRACT.md` | Contrato de interfaz frontend↔backend: endpoints públicos, forma de respuesta, reglas de no divulgación. Issue #4. | Aprobado |
| `docs/SECURITY.md` | Modelo de seguridad del MVP: tokens, hashing, rate limiting, CORS, headers, auditoría, backups. Issue #5. | Aprobado |
| `docs/WORKFLOW.md` | Flujo de colaboración: roles, ramas, Issues, ownership, protección de contratos compartidos. Issue #6. | Aprobado |
| `docs/COLLABORATION_PROMPT.md` | Prompt maestro de colaboración entre Alexis, ChatGPT y Claude — reglas base de trabajo conjunto. | Aprobado |

## 2. Decisiones importantes congeladas

Las siguientes decisiones quedan cerradas para implementación (detalle
completo en `docs/DECISIONS.md` y en los documentos referenciados; no
se reabren aquí):

- Dominio oficial: `artesanfc.com`.
- Frontend: Cloudflare Pages.
- Backend: FastAPI en servidor propio, detrás de Nginx.
- Base de datos: PostgreSQL.
- Rutas públicas: `/artesanos/{slug}`, `/piezas/{slug}`.
- Tres experiencias separadas: perfil público de artesano, pieza
  pública y certificado privado.
- El certificado no es descubrible mediante navegación pública, ni
  aparece en buscador interno ni en sitemap.
- `piece.id` ≠ `piece.public_code` ≠ `nfc_tag.physical_uid` ≠ token
  privado del certificado — cuatro conceptos distintos (ADR-008).
- Token de certificado: 256 bits vía CSPRNG, Base64 URL-safe sin
  padding; se persiste únicamente `token_hash = SHA-256(token)`.
- Estados públicos de la API de certificado: únicamente `authentic` /
  `unavailable`.
- NTAG213 no ofrece anti-clonación criptográfica — declarado
  explícitamente, no se promete lo contrario.
- El UID del NFC no es un factor de autenticación del sistema.
- Historial de certificados: una pieza puede tener múltiples
  certificados históricos; como máximo uno `active` a la vez.
- Propiedad de la pieza queda fuera del MVP (autenticidad primero,
  ADR-010).
- Tipografía v1: Instrument Serif (display) + Manrope (UI/texto).
- Dirección de Home: hero cinematográfico con video, layout
  predominantemente claro, sección de tecnología oscura, logo con
  desvanecimiento al hacer scroll, menú hamburguesa adaptativo.
- Flujo de Git: `main`, `develop`, `feature/*`, `fix/*`, `docs/*`.

## 3. Issues de GitHub completadas en Sprint 0

- **#2** — `docs/DESIGN_SYSTEM.md`
- **#3** — `docs/DATA_MODEL.md`
- **#4** — `docs/API_CONTRACT.md`
- **#5** — `docs/SECURITY.md`
- **#6** — `docs/WORKFLOW.md`

Además, tras la revisión de seguridad se completó una corrección
focalizada sin Issue numerada asignada explícitamente en este
documento: **historial y reemisión de certificados**
(`docs/DATA_MODEL.md` sección 14, rama `docs/certificate-history`),
que resolvió el `CROSS-DOCUMENT CHANGE REQUIRED` identificado por
`SECURITY.md` §14.1/§20.

No se inventan números de Issue para trabajo que no tuvo uno asignado
explícitamente.

## 4. Hallazgo cruzado de seguridad — RESUELTO

`docs/SECURITY.md` (Issue #5) identificó una incompatibilidad entre el
requisito de seguridad de poder revocar y reemitir un certificado sin
destruir su historial, y la restricción original de `DATA_MODEL.md`,
donde `certificate.piece_id` era **unique de forma global** (como
máximo un certificado en total por pieza, no uno activo).

**Resolución (RESUELTO, no pendiente):** `docs/DATA_MODEL.md` fue
actualizado para que:

- una pieza pueda tener **múltiples certificados históricos**;
- como máximo **uno** de esos certificados pueda estar `active` por
  pieza en cualquier momento (unique index parcial, mismo patrón ya
  usado para `nfc_tag`);
- los certificados `revoked` se **preserven** — nunca se borran ni se
  sobrescriben;
- un certificado de reemplazo reciba siempre un token completamente
  nuevo e independiente, nunca derivado del anterior.

Este era el único `CROSS-DOCUMENT CHANGE REQUIRED` pendiente entre
documentos de Sprint 0. No queda ninguno abierto.

## 5. Trabajo intencionalmente diferido

Lo siguiente **no** es trabajo incompleto de Sprint 0: es alcance
explícitamente diferido por diseño, documentado como tal en los
propios documentos aprobados (`SECURITY.md` §9, `API_CONTRACT.md`
§14, `DATA_MODEL.md` §3, `PROJECT.md` §4).

- Mecanismo exacto de autenticación administrativa.
- Contrato completo de la API administrativa (`/api/v1/admin/...`).
- Propiedad de la pieza y transferencias de propiedad.
- Paginación real (el MVP usa una envoltura `data`/`meta` simple).
- Pipeline de CI/CD completo.
- Implementación del despliegue en producción.
- Migraciones/implementación de base de datos.
- Implementación de frontend.
- Implementación de backend.
- Producción de assets 3D/360.
- Bloqueo (`lock`) y programación de NFC en producción.
- Cualquier afirmación de cumplimiento legal/regulatorio.

## 6. Listo para Sprint 1

Criterios de entrada verificados:

- [x] Sistema de diseño aprobado (`DESIGN_SYSTEM.md`).
- [x] Arquitectura de información disponible (rutas públicas, flujo de
      Home, estructura de artesano/pieza).
- [x] Frontera frontend/backend documentada (`ARCHITECTURE.md`,
      `API_CONTRACT.md`).
- [x] Formas de datos públicos conocidas (`API_CONTRACT.md` secciones
      4-8).
- [x] Fronteras de datos sensibles a seguridad conocidas
      (`SECURITY.md`, `API_CONTRACT.md` sección 11).
- [x] Flujo de Git operativo (`main` / `develop` / `feature/*` /
      `fix/*` / `docs/*`, `WORKFLOW.md`).
- [x] Rama `develop` limpia y al día.
- [x] Sin `PROPOSED DECISION` abierto que bloquee el frontend de Home.

**READY FOR SPRINT 1: YES**

## 7. Punto de entrada de Sprint 1

**Sprint 1 — Home frontend.**

Alcance sugerido:

1. Baseline/limpieza de la carpeta `frontend/`.
2. Tokens globales, tipografía y sistema de layout.
3. Header + menú hamburguesa adaptativo.
4. Sección de hero con video.
5. Sección de intro/manifiesto.
6. Sección de interacción NFC.
7. Sección de piezas destacadas.
8. Sección de artesanos.
9. Sección oscura de tecnología.
10. Historia/nosotros.
11. Contacto.
12. Footer.
13. Pase de responsive + `prefers-reduced-motion` + accesibilidad.

Usar contenido mock/fixture donde sea necesario. No depende de la
implementación del backend (`ARCHITECTURE.md` §9: `data.json` como
fixture temporal está permitido, nunca como fuente de verdad final).

### Definition of Done inicial — Sprint 1 (Home)

- Funciona en desktop y móvil.
- Sigue `docs/DESIGN_SYSTEM.md`.
- Sin errores de consola.
- Navegable por teclado.
- Soporta `prefers-reduced-motion`.
- Assets razonablemente optimizados.
- Sin dependencia dura de la API de producción.
- No expone datos de certificado ni datos privados.
- Revisado visualmente por Alexis.
- Mergeado a `develop` mediante PR.

## 8. Resultado de Sprint 0

ArtesaNFC cuenta ahora con contratos técnicos y de producto aprobados
suficientes para comenzar la implementación sin que cada colaborador
tenga que inventar arquitectura de forma independiente. El modelo de
datos, el contrato de API, el modelo de seguridad, el sistema de
diseño y el flujo de colaboración están alineados entre sí, sin
`PROPOSED DECISION` ni `CROSS-DOCUMENT CHANGE REQUIRED` abiertos.

Sprint 1 puede comenzar de inmediato con el frontend de Home.
