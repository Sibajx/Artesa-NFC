# Sprint 2 — Artesanos y Piezas Frontend — Cierre

**Estado:** COMPLETED
**Fecha de referencia:** 2026-09-17
**Alcance:** implementación de las páginas públicas de artesano y pieza de
ArtesaNFC (frontend estático, sin dependencia dura del backend), sobre la
base construida en Sprint 1.

Este documento cierra formalmente Sprint 2. Marca la finalización del
sprint de **Artesanos y piezas frontend**, no del MVP completo de
ArtesaNFC. Las páginas se implementaron con contenido mock/fixture
(`ARCHITECTURE.md` §9), sin backend/API, certificado ni datos reales de
artesano/pieza — eso queda para sprints posteriores según la secuencia
definida en `WORKFLOW.md` §14. Lo que Sprint 2 entrega es el directorio de
artesanos, el catálogo de piezas y sus páginas de detalle, con navegación
bidireccional artesano ↔ pieza completa, reutilizando el sistema de
diseño, motion y accesibilidad ya construido en Sprint 1.

## 1. Objetivo

Construir la experiencia pública de artesano y pieza: directorio de
artesanos, catálogo de piezas, perfiles individuales de artesano y páginas
individuales de pieza, con navegación bidireccional completa, siguiendo
`DESIGN_SYSTEM.md` §10-11 y las rutas objetivo de `PROJECT.md` §7 /
`ARCHITECTURE.md` §5.

## 2. Issues completadas en Sprint 2

| Issue | Alcance | PR |
|---|---|---|
| **#29** | Artisan and piece listing pages — `/artesanos` y `/piezas`, tarjetas reutilizando el sistema de Sprint 1. | #30 |
| **#31** | Artisan detail pages — 3 perfiles ficticios, estructura de `DESIGN_SYSTEM.md` §10. | #32 |
| **#33** | Piece detail pages — 4 páginas ficticias, enlace obligatorio "Creada por [artesano] →". | #34 |
| **#35** | Responsive, accessibility and final polish — auditoría de cierre de sprint, corrección de orden de secciones en páginas de pieza. | #36 |

Issue de seguimiento del sprint completo: **#28** — Sprint 2 — Artesanos y
piezas frontend.

Corrección puntual incluida en el cierre (Issue #35): el orden de
secciones de las 4 páginas de pieza no coincidía con el flujo documentado
en `DESIGN_SYSTEM.md` §11 (Galería aparecía después de Historia/Materiales
en vez de inmediatamente después del hero). Se corrigió reordenando el
marcado existente, sin tocar contenido, IDs, `aria-labelledby`, CSS ni
JavaScript (commit `90e1538`, PR #36).

## 3. Alcance completado

- Directorio de artesanos (`/artesanos`).
- Catálogo de piezas (`/piezas`).
- 3 páginas de detalle de artesano, ficticias.
- 4 páginas de detalle de pieza, ficticias.
- Relación bidireccional artesano ↔ pieza (`ADR-003`, `PROJECT.md` §6).
- Navegación de piezas relacionadas en cada página de pieza.
- Pase final de responsive/accesibilidad sobre las 9 rutas nuevas.

## 4. Rutas implementadas

Listados:

```text
/artesanos
/piezas
```

Artesanos:

```text
/artesanos/artesano-demo-01/
/artesanos/artesana-demo-02/
/artesanos/artesano-demo-03/
```

Piezas:

```text
/piezas/mascara-demo-01/
/piezas/figura-tallada-demo-01/
/piezas/textil-demo-01/
/piezas/vasija-demo-01/
```

Todas las rutas son consistentes con `PROJECT.md` §7 y `ARCHITECTURE.md`
§5. Ningún artesano ni pieza real fue registrado en este sprint
(`PROJECT.md` §11: 2 artesanos y 4 piezas reales siguen pendientes de
contenido documentado, sin relación con las entidades demo de este
sprint).

> **Nota posterior (F-08):** las 3 páginas de artesano y las 4 de pieza de esta
> sección se **eliminaron** del repositorio. `/artesanos/{slug}` y
> `/piezas/{slug}` se sirven ahora con un shell neutro y el contenido llega solo
> de la API (`ARCHITECTURE.md` §5, `SPRINT_4.md` §17). Lo que sigue se conserva
> como registro histórico de Sprint 2.

## 5. Política de contenido fixture

El contenido de Sprint 2 es explícitamente ficticio/de muestra:

- Los 3 perfiles de artesano y las 4 páginas de pieza son contenido demo,
  creado únicamente para validar estructura y navegación.
- Ninguno representa a una persona real, a una comunidad específica ni a
  una pieza física real — cada página lo declara en su propio texto
  (`section-note`).
- El contenido de producción real (biografías, historia documentada,
  fotografía y datos de las 2 artesanas/artesanos y 4 piezas reales de
  `PROJECT.md` §11) reemplazará este fixture en un sprint posterior,
  cuando exista integración con el backend (Sprint 3+).
- Esto sigue el mismo criterio ya aprobado en `ARCHITECTURE.md` §9: los
  fixtures son contenido temporal permitido, nunca fuente final de
  verdad.

## 6. Frontera de datos públicos/privados

Verificado explícitamente sobre las 9 rutas de este sprint: ninguna página
pública expone, referencia ni implica:

- token de certificado;
- estado de certificado;
- UID de NFC;
- presencia de NFC;
- ownership/propiedad;
- comportamiento de `/c/{token}`.

La representación pública de artesano y pieza en este frontend no incluye
ningún campo de la lista prohibida de `API_CONTRACT.md` §5/§11
(`has_certificate`, `certificate_id`, `certificate_status`, `has_nfc`, ni
ningún dato de `nfc_tag`). Consistente con `PROJECT.md` §2.3 y ADR-006: la
experiencia pública de la pieza permanece completamente separada de la
experiencia privada del certificado.

## 7. Implementación de UX/diseño

- Reutilización íntegra del sistema de diseño de Sprint 1: tokens,
  tipografía (Instrument Serif / Manrope), paleta, espaciado y grid — sin
  variables ni convenciones nuevas.
- HTML estático, sin build step ni framework, igual que Home.
- Header, drawer off-canvas y footer compartidos, idénticos en las 9
  rutas nuevas (mismo markup, mismos IDs, misma instancia de `main.js`).
- Sistema de tarjetas (`.card`, `.card-grid`) reutilizado sin cambios
  entre Home, listados y galerías de detalle.
- Estructura de página de artesano (`DESIGN_SYSTEM.md` §10): retrato →
  nombre/comunidad → historia → proceso/técnica → galería → piezas
  creadas. Implementada en ese orden en las 3 páginas.
- Estructura de página de pieza (`DESIGN_SYSTEM.md` §11): hero →
  galería → historia → materiales/técnica → artesano → piezas
  relacionadas. Implementada en ese orden final en las 4 páginas, tras la
  corrección de Issue #35 (sección 2). "Interacción especial" (3D/360) no
  se implementa en este sprint — punto de montaje futuro documentado
  inline, sin control visible (`DESIGN_SYSTEM.md` §12, fuera de alcance
  de Sprint 2).
- Enlace obligatorio "Creada por [Nombre del artesano] →" presente en las
  4 páginas de pieza (`DESIGN_SYSTEM.md` §11).

## 8. Validación de accesibilidad y responsive

| Verificación | Resultado |
|---|---|
| Un solo `<h1>` por página, jerarquía de encabezados sin saltos | PASS |
| Secciones semánticas con `aria-labelledby` correctamente resuelto | PASS |
| Skip link (`#main-content`) funcional en las 9 rutas | PASS |
| Hamburguesa `aria-expanded`/`aria-controls` sincronizados | PASS |
| Drawer: focus trap, Escape, cierre por backdrop, retorno de foco | PASS |
| Fondo (`#main-content` + footer) `inert`/`aria-hidden` con drawer abierto | PASS |
| Navegación completa por teclado | PASS |
| `prefers-reduced-motion` (CSS + JS, hero video y header) | PASS |
| Fallback `<noscript>` para `.fade-in` | PASS |
| Responsive en 360/390/768/1024/1200px+ y widescreen | PASS |
| Sin overflow horizontal en ningún ancho verificado | PASS |
| Touch targets ≥44px (hamburguesa, enlaces editoriales, nav) | PASS |
| Chromium — comportamiento de scroll y motion | PASS |
| Firefox — comportamiento de scroll y motion | PASS (ver sección 9) |
| Enlaces internos (listado ↔ detalle, artesano ↔ pieza, relacionadas) | PASS |
| Frontera de datos públicos/privados (sección 6) | PASS |

## 9. Notas conocidas no bloqueantes

- **Hitch de scroll en Firefox alrededor de ~768px de ancho**: investigado
  extensamente durante el cierre de sprint (Issue #35) y reproducido como
  comportamiento específico del compositor de ese navegador/entorno, no
  como una regresión de código del proyecto. Bajo el mismo escenario,
  Chromium se mantuvo fluido. Se documenta como no bloqueante; no se
  reabre salvo que aparezca nueva evidencia de una regresión real de
  código.
- **`favicon.ico`** — sigue pendiente del logo oficial de ArtesaNFC (404
  conocido desde Sprint 1, Issue #25; sin cambios en Sprint 2).
- **Medios y contenido real** — fotografía, video e información
  documentada de los artesanos y piezas reales de `PROJECT.md` §11 siguen
  pendientes; este sprint usa exclusivamente fixture (sección 5).
- **Contenido fixture de Home (Sprint 1) no alineado con las entidades
  demo de Sprint 2**: las tarjetas de "Piezas destacadas" y "Artesanos" en
  Home usan nombres, materiales y localidades distintos de los definidos
  en `/artesanos` y `/piezas` (p. ej. Chiapas/Michoacán en Home frente a
  Oaxaca en todo el fixture de Sprint 2), y enlazan de forma genérica a
  `/piezas`/`/artesanos` en vez de a los slugs específicos que ya existen.
  No es un enlace roto — ambos destinos resuelven correctamente — y Home
  es alcance ya cerrado de Sprint 1; se deja documentado para una
  actualización posterior, no bloquea el cierre de Sprint 2.

## 10. Explícitamente diferido

Lo siguiente no es trabajo incompleto de Sprint 2: es alcance
explícitamente diferido a sprints posteriores, según la secuencia de
`WORKFLOW.md` §14.

- Integración backend/API (Sprint 3).
- PostgreSQL como fuente de verdad.
- Datos reales de artesano/pieza.
- Integración NFC.
- Resolución de certificado (`POST /api/v1/certificates/resolve`).
- Propiedad/ownership de pieza.
- Implementación 3D/360 (punto de montaje reservado, sin control visible).
- Medios de producción reales (fotografía, video).

## 11. Listo para la siguiente fase

Criterios de entrada verificados:

- [x] Directorio de artesanos y catálogo de piezas funcionales, sin
      errores de consola.
- [x] 3 perfiles de artesano y 4 páginas de pieza, navegables en ambas
      direcciones.
- [x] Sigue `docs/DESIGN_SYSTEM.md` §10-11 (orden final de secciones
      verificado tras Issue #35).
- [x] Navegable por teclado; focus trap, Escape y backdrop correctos en
      las 9 rutas nuevas.
- [x] `prefers-reduced-motion` soportado de extremo a extremo.
- [x] Sin dependencia dura de la API de producción.
- [x] No expone datos de certificado, NFC ni datos privados (sección 6).
- [x] Rama `develop` limpia y al día con `origin/develop`.
- [x] Sin `PROPOSED DECISION` abierto que bloquee la siguiente fase.
- [x] Sin `CROSS-DOCUMENT CHANGE REQUIRED` abierto.

Sprint 2 está listo para la siguiente fase planeada en la secuencia de
`WORKFLOW.md` §14. Este documento no define ni anticipa el alcance
detallado de esa fase — eso se documentará en su propio cierre, cuando
corresponda.

## 12. Resultado de Sprint 2

ArtesaNFC cuenta ahora con un directorio de artesanos, un catálogo de
piezas y sus páginas de detalle, completamente navegables entre sí,
construidos sobre el sistema de diseño, motion y accesibilidad ya
validado en Sprint 1. La única corrección de sustancia encontrada durante
el cierre (orden de secciones en las páginas de pieza, Issue #35) fue
identificada mediante auditoría de solo lectura y resuelta con un cambio
de marcado mínimo y de bajo riesgo (PR #36). No quedan `PROPOSED DECISION`
ni `CROSS-DOCUMENT CHANGE REQUIRED` abiertos.
