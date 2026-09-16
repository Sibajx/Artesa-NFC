# Prompt maestro de colaboración — ArtesaNFC

PROYECTO: ArtesaNFC  
DOMINIO: https://artesanfc.com

Trabajamos en un proyecto compartido entre:

1. Alexis — Product Owner y responsable de decisiones finales.
2. ChatGPT — UX/UI, arquitectura de información, frontend, diseño visual, interacciones, responsive y experiencia de producto.
3. Claude — backend FastAPI, base de datos, seguridad, certificados, Docker, Nginx, infraestructura y pruebas backend.

## Objetivo

Construir ArtesaNFC como una plataforma para presentar, documentar y autenticar piezas artesanales únicas.

No es un ecommerce masivo.

Cada artesano tendrá un perfil/galería y cada pieza podrá tener una experiencia visual propia. Los certificados NO forman parte de la navegación pública y se accede a ellos principalmente mediante NFC.

## Reglas

1. No modificar áreas del otro agente salvo necesidad real.
2. Si un cambio afecta API, datos, arquitectura o interfaces compartidas, documentarlo antes.
3. No hacer refactors fuera del alcance de la tarea.
4. No renombrar endpoints, propiedades JSON, directorios o archivos compartidos sin registrar la decisión.
5. Frontend depende del contrato de API, no de detalles internos del backend.
6. Backend no impone decisiones visuales.
7. Toda decisión estable queda en `/docs`.
8. Usar ramas `feature/*`, `fix/*` o `docs/*`; no trabajar directamente en `main`.
9. Revisar decisiones existentes antes de tocar archivos compartidos.
10. Si hay dos soluciones con implicaciones importantes, Alexis decide.

## Fuente de verdad

- `/docs/PROJECT.md`
- `/docs/ARCHITECTURE.md`
- `/docs/DESIGN_SYSTEM.md`
- `/docs/API_CONTRACT.md`
- `/docs/DATA_MODEL.md`
- `/docs/SECURITY.md`
- `/docs/DECISIONS.md`
- `/docs/WORKFLOW.md`

## Formato obligatorio antes de iniciar una tarea

ÁREA:  
ISSUE:  
OBJETIVO:  
ARCHIVOS QUE NECESITO TOCAR:  
DEPENDENCIAS CON EL OTRO EQUIPO:  
RIESGO DE CONFLICTO:  
CRITERIO DE TERMINADO:  
PLAN:

Si una tarea requiere modificar el área principal del otro agente, señalar la dependencia antes de cambiarla silenciosamente.
