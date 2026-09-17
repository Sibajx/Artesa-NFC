# Sprint 1 — Home Frontend — Cierre

**Estado:** COMPLETED
**Fecha de referencia:** 2026-09-17
**Alcance:** implementación de la Home pública de ArtesaNFC (frontend estático, sin dependencia dura del backend).

Este documento cierra formalmente Sprint 1. Marca la finalización del
sprint de **Home frontend**, no del MVP completo de ArtesaNFC. La Home
se implementó con contenido mock/fixture donde corresponde
(`ARCHITECTURE.md` §9), sin backend/API, certificado ni datos reales de
artesano/pieza — eso queda para sprints posteriores según la secuencia
definida en `WORKFLOW.md` §14. Lo que Sprint 1 entrega es una Home
funcional, responsive, accesible y con motion propio, lista para servir
de base a las páginas de artesano/pieza de Sprint 2.

## 1. Issues completadas en Sprint 1

| Issue | Alcance | PR |
|---|---|---|
| **#15** | Home frontend foundation — tokens, base, layout, estructura semántica, header + menú hamburguesa. | #16 |
| **#17** | Home content and visual sections — las 10 secciones de `DESIGN_SYSTEM.md` §16, jerarquía de encabezados. | #18 |
| **#19** | Home media and asset integration — `<picture>` WebP/JPEG, poster de hero, `aspect-ratio`, lazy loading. | #20 |
| **#21** | Responsive and visual polish — breakpoints, grillas responsive, ancho del panel de navegación. | #22 |
| **#23** | Home motion polish — reveal `.fade-in`, `prefers-reduced-motion`, header hide/show con histéresis, fade del logo. | #24 |
| **#25** | Performance and accessibility pass — `inert`/`aria-hidden` de fondo, scroll lock seguro en iOS, touch targets 44px, ajuste de histéresis. | #26 |

Además del trabajo de las seis Issues, un lote final de correcciones de
integración (posicionamiento responsive del Hero, posición vertical del
menú off-canvas, header fijo mientras el drawer está abierto y
compensación de su altura) se revisó y pusheó directamente a `develop`
en el commit `3b12aed` (sección 6), por indicación explícita de Alexis,
como excepción puntual al flujo normal de rama `feature/*` + PR
(`WORKFLOW.md` §3, §9). No se abrió Issue numerada nueva para este lote
— es continuación directa del alcance de la Issue #25 (mismo dominio:
performance/accesibilidad de la Home), no trabajo no relacionado.

## 2. Capacidades entregadas del frontend

- Estructura semántica completa: `header`/`nav`/`main`/`footer`,
  jerarquía de encabezados sin saltos, dos landmarks `nav` distinguibles
  por `aria-label`, skip link funcional.
- Header con logo + menú hamburguesa: fade del logo al hacer scroll,
  ocultar/mostrar con histéresis (ajustada de 12px a 8px), fijo al
  viewport mientras el drawer está abierto (con compensación de altura
  para no producir salto de layout).
- Menú off-canvas: `aria-expanded`/`aria-controls`/`aria-label`
  sincronizados, focus trap, cierre por Escape/backdrop/click en link,
  fondo (`#main-content` + footer) marcado `inert`/`aria-hidden`
  mientras está abierto, scroll lock seguro en iOS con restauración
  exacta de la posición de scroll, posición vertical del grupo de
  enlaces ajustada (upper-middle en <1200px).
- Hero cinematográfico 100vh: poster + `<video>` (fuente real diferida,
  Issue #19), posicionamiento del contenido ajustado a tercio superior
  en móvil/tablet, sin afectar el llenado completo de `.hero__media` ni
  el comportamiento en desktop (≥1200px sin cambios).
- Las 10 secciones de Home (`DESIGN_SYSTEM.md` §16) con contenido
  mock/fixture, tarjetas con `<picture>` WebP+JPEG, `loading="lazy"`,
  `aspect-ratio` + `width`/`height` explícitos.
- Sistema de motion: reveal por `IntersectionObserver`, fallback
  `<noscript>`, soporte completo de `prefers-reduced-motion` en CSS y
  JS.
- Touch targets ajustados a 44px mínimo (`.editorial-link`,
  `.nav-panel__link`, hamburguesa) sin alterar la composición visual.
- Cabeceras de seguridad base en `frontend/_headers`
  (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`,
  `Permissions-Policy`).

## 3. Matriz de validación responsive

| Ancho/caso | Resultado |
|---|---|
| 360px | PASS |
| 390px | PASS |
| 768px | PASS |
| 1024px | PASS |
| 667×375 (landscape) | PASS |
| Desktop (≥1200px) | PASS |
| Posicionamiento responsive del Hero | PASS |
| Posición vertical del menú off-canvas | PASS |
| Sin overflow horizontal | PASS |

## 4. Validación de accesibilidad y performance

| Verificación | Resultado |
|---|---|
| Header + botón cerrar visibles con el drawer abierto | PASS |
| Apertura/cierre del drawer con scroll profundo | PASS |
| Sin salto de layout al abrir/cerrar el menú | PASS |
| Restauración exacta de la posición de scroll | PASS |
| Scroll lock en iOS Safari | PASS |
| Estabilidad del drawer en móvil | PASS |
| Focus trap / Escape / backdrop | PASS |
| Touch targets (44px) | PASS |
| Errores de consola (JS) | PASS |
| Sintaxis JS (`node --check`) | PASS |

## 5. Validación de motion/interacción

| Verificación | Resultado |
|---|---|
| Ocultar/mostrar header | PASS |
| Scroll ascendente lento (histéresis 8px) | PASS |
| Sin parpadeo (flicker) | PASS |
| Reveal de secciones (`.fade-in`) | PASS |
| Reveal de tarjetas | PASS |
| `prefers-reduced-motion` | PASS |

## 6. Commit final integrado

```
3b12aedb32f25074982fa0b44a1bbe5bdd4e9508
fix: finalize Home responsive interactions
```

Archivos de este commit final: `frontend/assets/css/components.css`,
`frontend/assets/css/pages.css`, `frontend/assets/js/main.js`.
Rama `develop`, sincronizada con `origin/develop`; `main` no fue
tocada.

## 7. Trabajo intencionalmente diferido

Lo siguiente **no** es trabajo incompleto de Sprint 1: es alcance
explícitamente diferido, ya conocido y aceptado, sin bloquear el cierre
del sprint.

- `favicon.ico` — pendiente del logo oficial de ArtesaNFC (404 conocido
  y documentado, Issue #25).
- Video real del Hero (Issue #19 — sin encoder local disponible).
- Fotografía de producción real.
- Generación de AVIF.
- Integración 3D.
- Integración backend/API.
- Datos reales de artesano/pieza.
- Flujo de certificado.
- Self-hosting de Google Fonts (decisión aprobada: mantener CDN de
  Google Fonts para Sprint 1; reevaluar tras mediciones reales de
  Lighthouse/runtime).
- Política CSP completa en `frontend/_headers`.

## 8. Trade-offs conocidos no bloqueantes

- El `.logo` dentro del header permanece alcanzable por navegación de
  lector de pantalla (swipe/cursor virtual) mientras el drawer está
  abierto — consecuencia de excluir todo el header de `inert` porque
  contiene el botón de cierre; documentado, no bloqueante.
- Texto alternativo repetido ("Marcador de posición — contenido
  pendiente") en las 6 tarjetas mock — aceptable en la etapa actual de
  contenido fixture, se resuelve naturalmente cuando llegue contenido
  real en Sprint 2/3.
- `--header-height` (compensación de altura del header durante el
  scroll lock) se mide una vez al abrir el drawer; si el viewport
  cambia de tamaño mientras el drawer permanece abierto (p. ej. rotar
  el teléfono), la compensación no se re-mide hasta el siguiente
  ciclo de apertura/cierre. Interacción de baja probabilidad, aceptada
  sin listener de `resize` adicional.

## 9. Listo para Sprint 2

Criterios de entrada verificados:

- [x] Home funcional en desktop y móvil, sin errores de consola.
- [x] Sigue `docs/DESIGN_SYSTEM.md` (paleta, tipografía, layout,
      navegación, hero, motion, accesibilidad, performance).
- [x] Navegable por teclado; focus trap, Escape y backdrop correctos.
- [x] `prefers-reduced-motion` soportado de extremo a extremo.
- [x] Sin dependencia dura de la API de producción.
- [x] No expone datos de certificado ni datos privados.
- [x] Rama `develop` limpia y al día con `origin/develop`.
- [x] Sin `PROPOSED DECISION` abierto que bloquee Sprint 2.
- [x] Sin `CROSS-DOCUMENT CHANGE REQUIRED` abierto.

**READY FOR SPRINT 2: YES**

## 10. Punto de entrada de Sprint 2

**Sprint 2 — Perfiles de artesano y páginas de pieza**, según la
secuencia de `WORKFLOW.md` §14 y las rutas objetivo de `PROJECT.md` §7 /
`ARCHITECTURE.md` §5:

1. Página `/artesanos` (directorio) y `/artesanos/{slug}` (perfil),
   estructura de `DESIGN_SYSTEM.md` §10.
2. Página `/piezas/{slug}` (pieza pública), estructura de
   `DESIGN_SYSTEM.md` §11, incluyendo el enlace obligatorio
   "Creada por [Nombre del artesano] →".
3. Reutilizar el sistema de tokens/CSS/motion/accesibilidad ya
   construido en Sprint 1 en vez de reinventarlo por página.
4. Contenido mock/fixture donde aún no haya datos reales, mismo
   criterio que en Sprint 1 (`ARCHITECTURE.md` §9).
5. Navegación bidireccional artesano ↔ pieza (enlaces ya reservados
   en la nav de Home: `/artesanos`, `/piezas`).
6. Mismo pase de responsive + `prefers-reduced-motion` + accesibilidad
   que se aplicó a Home, verificado en los mismos anchos (360/390/
   768/1024/1440).

No depende de la implementación del backend; puede continuar con
fixtures hasta que Sprint 3 (integración FastAPI/API) esté listo.

## 11. Resultado de Sprint 1

ArtesaNFC cuenta ahora con una Home pública completa, responsive,
accesible y con motion propio, construida sobre el sistema de diseño
aprobado en Sprint 0 sin necesitar backend. El header, el menú
off-canvas, el hero y el scroll lock fueron endurecidos frente a
regresiones reales encontradas durante la validación (header perdido
tras el scroll lock, salto de layout al fijar el header, posición del
contenido del Hero y del menú en móvil/tablet) — todas resueltas con
el commit final `3b12aed`. No quedan `PROPOSED DECISION` ni
`CROSS-DOCUMENT CHANGE REQUIRED` abiertos.

Sprint 2 puede comenzar con los perfiles de artesano y las páginas de
pieza.
