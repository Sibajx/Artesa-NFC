# ArtesaNFC — Workflow de colaboración

**Estado:** Aprobado para MVP
**Issue:** #6 — docs: create WORKFLOW.md
**Fecha de referencia:** 2026-09-16

## 0. Propósito y alcance

Este documento define el flujo práctico de colaboración y entrega entre
Alexis, ChatGPT y Claude/Claude Code, para que ninguno sobrescriba el
trabajo del otro ni cambie un contrato compartido en silencio.

Es operativo, no teórico: describe cómo se trabaja día a día, no
introduce un framework de gobernanza nuevo. No rediseña producto,
arquitectura, seguridad ni modelo de datos — esos ya están aprobados en
`PROJECT.md`, `ARCHITECTURE.md`, `DATA_MODEL.md`, `API_CONTRACT.md`,
`SECURITY.md` y `DECISIONS.md`. Este documento tampoco redefine los
roles o el modelo de ramas ya aceptados en `PROJECT.md` §12-13 y
`DECISIONS.md` ADR-022/ADR-023; los documenta con más detalle operativo.

## 1. Roles

### Alexis — Product Owner

- Define visión y prioridades.
- Toma las decisiones finales de producto.
- Coordina contenido de artesanos y validación en el mundo real.
- Aprueba decisiones importantes de diseño, producto y seguridad.
- Revisa y mergea pull requests.
- Es dueño de la aceptación del trabajo terminado.

### ChatGPT — Frontend / UX / diseño

Responsabilidades principales:

- arquitectura de información
- UX/UI
- dirección visual
- storytelling
- arquitectura frontend
- HTML/CSS
- JavaScript de frontend
- comportamiento responsive
- accesibilidad
- performance de frontend
- presentación 3D/360 en cliente
- consistencia del sistema de diseño

### Claude / Claude Code — Backend / infraestructura / seguridad

Responsabilidades principales:

- FastAPI
- PostgreSQL
- modelos/schemas
- migraciones
- servicios de backend
- lógica de tokens de certificado
- implementación de seguridad en backend
- backend administrativo
- Docker
- Nginx
- despliegue en servidor
- pruebas de backend
- logs y aspectos operativos de backend

### Áreas compartidas

- contrato de API
- integración
- comportamiento end-to-end
- flujo de certificado
- performance entre frontend y backend
- CI/CD cuando se introduzca
- decisiones de arquitectura que cruzan documentos

## 2. Fuente de verdad

`/docs` es la fuente de verdad para arquitectura y contratos de producto
aprobados. Documentos relevantes:

- `PROJECT.md`
- `ARCHITECTURE.md`
- `DECISIONS.md`
- `DESIGN_SYSTEM.md`
- `DATA_MODEL.md`
- `API_CONTRACT.md`
- `SECURITY.md`
- `WORKFLOW.md` (este documento)
- `SPRINT_0.md`
- `COLLABORATION_PROMPT.md`

Reglas:

- La implementación debe seguir los documentos aprobados.
- Si la implementación descubre un conflicto con un documento aprobado,
  no se cambia el comportamiento en silencio: el conflicto se documenta
  primero (sección 6).
- Los cambios a un contrato compartido requieren coordinación antes de
  implementarse.

## 3. Modelo de ramas Git

```text
main
- producción/estable
- nunca se usa para trabajo del día a día
- recibe cambios desde develop mediante PRs de release controlados

develop
- rama de integración
- el trabajo terminado se mergea aquí vía PR

ramas de trabajo:
- feature/*
- fix/*
- docs/*
```

Reglas:

- Nunca trabajar directamente en `main`.
- Preferiblemente no trabajar directamente en `develop`.
- Crear una rama por Issue/tarea.
- La rama debe partir de un `develop` actualizado.
- Un concern lógico por rama, donde sea práctico.

## 4. Trabajo dirigido por Issues

Todo cambio relevante debe tener una Issue de GitHub.

La Issue debe contener:

- objetivo
- alcance
- archivos/área esperados
- criterios de aceptación
- dependencias
- elementos fuera de alcance, cuando aplique

Antes de empezar a trabajar, se usa la siguiente plantilla obligatoria:

```text
AREA:
ISSUE:
OBJETIVO:
FILES TO MODIFY:
DEPENDENCIES:
CONFLICT RISK:
DEFINITION OF DONE:
PLAN:
```

Por qué importa:

- evita superposición de trabajo;
- expone dependencias temprano;
- facilita las revisiones;
- evita que un agente entre silenciosamente al área de otro dueño.

## 5. Ownership de cambios / reglas de conflicto

- Un dueño principal por área/tarea.
- Otro agente puede revisar o asesorar, pero no debe reescribir
  silenciosamente el área del dueño.
- Los archivos/contratos compartidos requieren coordinación.
- Si una tarea requiere cambiar el área de otro equipo, se marca como
  dependencia antes de tocarla.
- No se hacen ediciones oportunistas no relacionadas en la misma rama.
- Si se encuentra un problema no relacionado, se abre/documenta una
  Issue separada.

Ejemplos:

- Claude trabajando en backend no debe modificar `DESIGN_SYSTEM.md`.
- ChatGPT trabajando en frontend no debe redefinir restricciones de base
  de datos.
- Ninguno de los dos debe cambiar `API_CONTRACT.md` en silencio mientras
  implementa su propio lado.

## 6. Documentar decisiones primero

Convenciones:

**PROPOSED DECISION**
Se usa cuando se encuentra una decisión real de producto/arquitectura/
seguridad sin resolver. El trabajo se pausa en ese punto si afecta
comportamiento compartido, hasta que Alexis la apruebe.

**CROSS-DOCUMENT CHANGE REQUIRED**
Se usa cuando un requisito válido entra en conflicto con otro documento
ya aprobado. No se edita el otro documento en silencio: se crea una
Issue/rama de seguimiento enfocada.

**ADR**
Las decisiones de arquitectura/producto importantes y de largo plazo van
a `DECISIONS.md`. No todo detalle pequeño de implementación necesita un
ADR.

## 7. Flujo estándar de una tarea

1. Actualizar `develop`.
2. Crear la rama de Issue/tarea.
3. Leer los documentos relevantes.
4. Llenar la plantilla de inicio de tarea (sección 4).
5. Hacer cambios acotados al alcance.
6. Revisar el diff local.
7. Resolver los `PROPOSED DECISION` encontrados.
8. Resolver por separado las dependencias cruzadas de documentos, si
   aplica.
9. Agregar al stage solo los archivos previstos.
10. Commitear con un mensaje claro.
11. Pushear la rama.
12. Abrir PR hacia `develop`.
13. Revisar "Files changed".
14. Verificar checks.
15. Mergear.
16. Borrar la rama.
17. Sincronizar `develop` local.
18. Mover la Issue/tarjeta del proyecto a Done.

## 8. Convención de commits

Prefijos simples recomendados:

```text
docs:
feat:
fix:
refactor:
test:
chore:
```

Ejemplos:

```text
docs: define ArtesaNFC API contract
feat: add public artisan endpoint
fix: prevent revoked certificate resolution
test: add certificate resolution tests
```

No se exige un sistema completo de Conventional Commits.

## 9. Reglas de Pull Request

Los PR deben:

- apuntar a `develop` para trabajo normal;
- enlazar/cerrar la Issue correspondiente cuando aplique;
- contener un resumen conciso;
- contener solo los archivos previstos;
- pasar los checks antes de mergear;
- revisarse por alcance y consistencia de contrato.

Antes de mergear, verificar:

- la rama base es la correcta;
- los archivos cambiados son los esperados;
- no hay secretos accidentales;
- no hay archivos no relacionados;
- no hay `PROPOSED DECISION` sin resolver;
- no hay conflicto cruzado de documentos sin resolver.

`develop` → `main`:

- solo para release de producción;
- PR de release separado;
- no forma parte del cierre normal de una Issue.

## 10. Modelo de revisión

**Trabajo de backend/docs de Claude:**

Claude propone/crea el cambio → Alexis + ChatGPT revisan → correcciones
→ commit → PR → merge.

**Trabajo de frontend de ChatGPT:**

ChatGPT propone/construye el cambio de frontend → Alexis revisa visual y
de producto → Claude puede revisar integración/contrato de backend si es
relevante → PR → merge.

**Cambios de contrato compartido:**

Alexis, ChatGPT y Claude deben revisar cuando el cambio afecta
materialmente tanto a frontend como a backend.

## 11. Protección de archivos/contratos

Archivos de contrato compartido protegidos:

- `docs/ARCHITECTURE.md`
- `docs/DECISIONS.md`
- `docs/DATA_MODEL.md`
- `docs/API_CONTRACT.md`
- `docs/SECURITY.md`
- `docs/WORKFLOW.md`

Reglas:

- no se modifican en silencio como efecto secundario de una
  implementación;
- los cambios de documentación enfocados normalmente usan ramas
  `docs/*`;
- si la implementación requiere un cambio de contrato, se documenta y
  revisa primero (sección 6).

## 12. Pruebas / validación

Mínimos esperados antes de mergear:

**Documentación:**

- consistencia interna;
- sin marcadores de decisión sin resolver;
- referencias a otros documentos correctas.

**Frontend:**

- responsive;
- verificaciones básicas de teclado/accesibilidad;
- sin errores de consola;
- navegadores principales donde sea práctico;
- comportamiento de `reduced-motion` donde sea relevante.

**Backend:**

- pruebas para el comportamiento afectado;
- comportamiento de validación/error;
- sin secretos en logs;
- comportamiento sensible a seguridad probado.

**Integración:**

- las formas de respuesta del frontend coinciden con `API_CONTRACT.md`;
- el comportamiento del esquema del backend coincide con
  `DATA_MODEL.md`;
- el comportamiento de seguridad coincide con `SECURITY.md`.

No se inventa un pipeline de CI completo todavía.

## 13. Project board

Estados actuales del proyecto:

```text
Todo
In Progress
Done
```

Uso práctico:

- **Todo:** no iniciado / listo para iniciar.
- **In Progress:** rama/trabajo activo en curso.
- **Done:** mergeado/aceptado.

Una Issue no está Done solo porque el código existe localmente;
normalmente pasa a Done después del merge/aceptación.

## 14. Trabajo diario / de sprint

Cadencia ligera:

- tareas pequeñas y verificables;
- 1-3 tareas significativas por sesión/día de trabajo;
- entregable utilizable semanal donde sea práctico;
- evitar abrir demasiadas ramas en paralelo;
- terminar/mergear trabajo antes de iniciar trabajo no relacionado,
  cuando sea posible.

Secuencia actual de alto nivel:

```text
Sprint 0 — fundaciones/documentación
Sprint 1 — Home frontend
Sprint 2 — páginas de artesano y pieza
Sprint 3 — integración FastAPI/API
Sprint 4 — flujo NFC/certificado privado
Sprint 5 — piloto 3D/360
```

No se congelan fechas.

## 15. Emergencia / hotfix

Para correcciones urgentes de producción:

- crear rama `fix/*` desde la base de producción correcta;
- documentar la Issue;
- revisar;
- mergear a `main` según se necesite;
- asegurar que la corrección también se refleje de vuelta en `develop`.

No se sobre-diseña la gestión de releases todavía.

## 16. Qué NO incluye este documento

Este documento no:

- redefine arquitectura de producto;
- redefine decisiones de seguridad;
- redefine API/modelo de datos;
- crea un framework de gobernanza empresarial completo;
- introduce burocracia innecesaria;
- inventa herramientas que aún no existen.

## 17. Resumen de `PROPOSED DECISION`

No quedan `PROPOSED DECISION` abiertos en este documento.

## 18. Resumen de `CROSS-DOCUMENT CHANGE REQUIRED`

No se identifica ningún `CROSS-DOCUMENT CHANGE REQUIRED` en este
documento. El contenido aquí es consistente con `PROJECT.md` §12-13,
`DECISIONS.md` (ADR-022, ADR-023) y `COLLABORATION_PROMPT.md`, y no
requiere modificar `DATA_MODEL.md`, `API_CONTRACT.md`, `SECURITY.md`,
`DESIGN_SYSTEM.md`, `DECISIONS.md` ni `PROJECT.md`.
