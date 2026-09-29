# ArtesaNFC — Registro de decisiones

Este archivo registra decisiones aceptadas para evitar reabrirlas en cada sesión sin nueva información.

## ADR-001 — Dominio oficial

**Estado:** Aceptado  
**Decisión:** `artesanfc.com` es el dominio oficial. No usar `artesanfc.lol`.

## ADR-002 — Sin subdominios por artesano

**Estado:** Aceptado  
**Decisión:** usar `artesanfc.com/artesanos/{slug}`.

Los subdominios quedan reservados para servicios técnicos, por ejemplo `api.artesanfc.com`.

## ADR-003 — Relación bidireccional artesano ↔ pieza

**Estado:** Aceptado  
**Decisión:** toda pieza enlaza al artesano y todo perfil de artesano muestra sus piezas.

## ADR-004 — Una pieza pertenece a un artesano en el MVP

**Estado:** Aceptado  
**Decisión:** cada pieza tiene un único `artisan_id` en el modelo inicial.

## ADR-005 — Separar perfil, pieza pública y certificado

**Estado:** Aceptado  
**Decisión:** son tres experiencias distintas.

## ADR-006 — Certificados fuera de navegación e indexación

**Estado:** Aceptado  
**Decisión:** no aparecen en menú, galerías, directorios, sitemap ni buscador público. Usan directivas de no indexación.

## ADR-007 — Tokens criptográficamente seguros

**Estado:** Aceptado  
**Decisión:** generar tokens con un CSPRNG probado.

No desplegar algoritmos propios basados en primos, secuencias o IDs como mecanismo principal de seguridad sin revisión criptográfica externa.

## ADR-008 — UID NFC separado del token

**Estado:** Aceptado

```text
ID de pieza != UID NFC != token privado
```

## ADR-009 — Rate limiting, logs y revocación

**Estado:** Aceptado  
**Decisión:** el flujo de certificados incluirá las tres capacidades.

## ADR-010 — Autenticidad primero, propiedad después

**Estado:** Aceptado para MVP  
**Decisión:** el MVP certifica autenticidad. Propiedad y transferencias se diseñan para futuro.

## ADR-011 — Piezas únicas

**Estado:** Aceptado  
**Decisión:** ArtesaNFC no se diseña como ecommerce masivo.

## ADR-012 — Estructura visual común + identidad por pieza

**Estado:** Aceptado  
**Decisión:** cada pieza puede tener paleta y recursos propios dentro del sistema general.

## ADR-013 — Home clara con contraste

**Estado:** Aceptado  
**Decisión:** predominio de blanco/claro con secciones oscuras deliberadas.

## ADR-014 — Hero con video

**Estado:** Aceptado  
**Decisión:** video optimizado con poster/fallback.

## ADR-015 — Menú dinámico

**Estado:** Aceptado  
**Decisión:** menú de tres líneas arriba a la derecha; se desvanece al bajar y reaparece al subir.

## ADR-016 — Logo con desvanecimiento

**Estado:** Aceptado  
**Decisión:** fuerte en hero, disminuye con el scroll.

## ADR-017 — Backend FastAPI separado

**Estado:** Aceptado  
**Decisión:** FastAPI + Nginx + base relacional en servidor.

## ADR-018 — Frontend en Cloudflare Pages

**Estado:** Aceptado inicialmente  
**Decisión:** `artesanfc.com` sirve frontend desde Cloudflare Pages.

## ADR-019 — No usar `data.json` como base de producción

**Estado:** Aceptado  
**Decisión:** datos dinámicos provienen del backend.

## ADR-020 — 3D/360 selectivo

**Estado:** Aceptado  
**Decisión:** usarlo solo cuando aporte valor. Caso piloto: máscara de Cuilápam de Guerrero.

## ADR-021 — Tags NFC actuales

**Estado:** Registrado  
**Hardware:** NTAG213, 13.56 MHz, ISO 14443A.

**Decisión:** nunca bloquear un tag destinado a una pieza antes de validar completamente su URL.

## ADR-022 — Workflow por Issues y ramas

**Estado:** Aceptado

```text
main       -> producción
develop    -> integración
feature/*  -> nuevas funciones
fix/*      -> correcciones
docs/*     -> documentación
```

## ADR-023 — Responsabilidad por áreas

**Estado:** Aceptado

- Alexis: Product Owner.
- ChatGPT: UX/UI, frontend y arquitectura de experiencia.
- Claude: backend e infraestructura.
- Compartido: API, integración, seguridad end-to-end y CI/CD.

## ADR-024 — Caso piloto

**Estado:** Aceptado  
**Decisión:** primer recorrido completo con artesano de Cuilápam + una máscara.
## ADR-025 — Tipografía inicial de ArtesaNFC

**Estado:** Aceptado para v1  
**Fecha:** 2026-09-16

**Decisión:** Utilizar:

```text
Display: Instrument Serif
UI / Texto: Manrope
```

**Motivo:** La combinación equilibra una presencia editorial y artesanal con una capa de interfaz contemporánea, limpia y legible. Instrument Serif aporta carácter a la narrativa y a las piezas; Manrope mantiene claridad en navegación, metadatos, certificados y contenido funcional.

**Reglas:**
- Instrument Serif se reserva para títulos, hero, nombres de piezas, nombres de artesanos y frases de alto impacto.
- Manrope se utiliza para navegación, cuerpo de texto, metadatos, botones, formularios, certificados y footer.
- Las páginas de pieza no deben introducir tipografías distintas de forma arbitraria.
- La tipografía puede revisarse en versiones futuras si existe una razón clara de identidad, legibilidad o producto.

**Consecuencias:** Esta decisión debe reflejarse en `docs/DESIGN_SYSTEM.md`. Un cambio futuro debe registrarse mediante una nueva ADR que reemplace o superseda esta decisión.

## ADR-026 — Provisioning de certificados y tags NFC por CLI local

**Estado:** Aceptado para el piloto

**Fecha:** 2026-09-20 (issue #107, N-09)

**Decisión:** la emisión de tokens y la programación de tags NFC se hacen con
una CLI interactiva (`python -m app.cli.provision`), ejecutada por SSH en el host
del backend/PostgreSQL, que reutiliza los servicios de ciclo de vida existentes.
No hay API administrativa (ni temporal), no se acepta el token por ningún canal de
entrada y solo se muestra la URL completa, una vez, en un terminal interactivo.
`AUDIT_EVENT` queda diferido; el bloqueo físico es un paso aparte y opcional.

**Alternativas descartadas:** comando no interactivo para tuberías con
herramientas NFC (el token viajaría por pipes, argv o volcados; sin hardware
definido), REPL manual (sin barreras contra errores) y API administrativa (el
contrato prohíbe el token en cualquier respuesta y faltan autenticación y
auditoría).

**Consecuencias:** el detalle operativo vive en `docs/PROVISIONING.md`. Crear
artesanos y piezas sigue fuera de este flujo. Una futura API administrativa o un
lector USB integrado serían decisiones nuevas.

## ADR-027 — Despliegues del backend inmutables, verificables y recuperables

**Estado:** Aceptado (N-08). Implementado en el repositorio; adopción en el
servidor pendiente de una ventana aprobada por el PO.

**Fecha:** 2026-09-23

**Contexto:** el incidente del 2026-09-20/21: producción no era un checkout de
Git (árbol híbrido de varias copias), el venv compartido derivó y dejó el
servicio en crash-loop, y otra aplicación que compartía directorio y venv tomó
el puerto 8000. No había forma de saber qué commit corría.

**Decisión:** artifact inmutable construido **fuera de producción** desde objetos
Git, solo desde historia alcanzable desde `origin/main` (D5, D7), con
`RELEASE.json`, `MANIFEST.sha256` y checksum SHA-256 (firma diferida, D6),
reproducible byte a byte desde el mismo commit (los tags de Git no forman
parte del artifact);
layout `bin/ incoming/ releases/ shared/ current previous` (D2) con un venv por
release instalado desde `requirements-prod.lock` (pip-tools, hashes, PyPI; D3,
D4, D16); herramienta estándar en `bin/` (D15) con fases explícitas,
`--dry-run` y fail-closed; `--expect-commit` obligatorio en producción;
migraciones `breaking` rechazadas (D9); antes de cualquier migración, backup
`pg_dump -Fc` y restore-check del mismo dump en un PostgreSQL desechable
(D10, D11) con ensayo de la migración sobre la copia; rollback automático
**solo** en despliegues `CODE_ONLY`, nunca tras una migración, y nunca
`downgrade` (D13); sudo interactivo o paso manual, sin `NOPASSWD` (D14);
`shared/.env` leído y compuesto por la herramienta (D18); retención de 5
releases (D12); evidencia por despliegue en
`shared/state/deployments/`; CI obligatorio en `main` que valida el release sin
desplegar (D17), con la suite ejecutada sobre las dependencias exactas del
lock de producción.

**Alternativas descartadas:** `git pull` en el servidor (el servidor no debe
tener credenciales ni árbol mutable), Docker (fuera del alcance del piloto),
despliegue automático desde CI, downgrade automático, un venv compartido.

**Consecuencias:** el detalle vive en `docs/DEPLOYMENT.md`; la unit objetivo en
`backend/ops/systemd/artesa-nfc.service.example`. Quedan abiertos: la firma del
artifact, los backups programados/fuera del host/cifrados (issue aparte, antes
del lanzamiento final) y la política de reinicio de la unit.

## ADR-028 — Frontend público en Astro, migración incremental

**Estado:** Aceptado para implementación en `web/`; **no desplegado**. La
adopción en producción requiere staging, aprobación visual y assets reales
autorizados (gate humano).

**Fecha:** 2026-09-28

**Contexto:** el frontend estático sin build (`frontend/`) no escala a la
experiencia prevista (hero de video, galería, visor 3D, pasaporte digital) sin
un sistema de componentes, tipado del contrato y control del peso de JS.

**Decisión:** nueva app en `web/` con Astro (salida estática, sin runtime de
servidor), TypeScript estricto, React **solo en islas** interactivas y
`@google/model-viewer` cargado **bajo demanda** como visor 3D (three.js nunca
global; three.js directo solo si model-viewer muestra una limitación concreta).
Se conservan sin cambios: la API y su contrato, el backend, la base de la API
por hostname exacto (`api-config`), el modelo de shells neutros de F-08 (la API
es la única autoridad de publicación; sin pre-renderizado de entidades), la
ruta privada `/c/{token}` con sus garantías (token solo en el path y en el
cuerpo del POST, `noindex`, `no-referrer`, `no-store`) y el único estado
público `unavailable` del certificado (no se muestra "revocado"). Rutas
desconocidas responden `404.html` con estado 404. `frontend/` permanece como
producción, referencia y rollback hasta que la nueva versión esté estable.

**Alternativas descartadas:** SSR en Cloudflare Pages Functions (nuevo runtime;
la resolución del certificado desde el borde rompería el rate limiting por IP
del visitante); pre-renderizar entidades en el build (segunda copia de los
datos, contra F-08/ADR-019); Three.js directo (más peso y código sin
necesidad demostrada); reescritura big-bang.

**Consecuencias:** `web/README.md` documenta arquitectura, estados, despliegue,
staging y rollback; CI propio (`.github/workflows/web-ci.yml`). Pendientes: capa
de media/CDN (PEND-033), assets reales autorizados, staging con su hostname en
`api-config` y CORS, y el cambio de configuración de build del proyecto de
Pages (dashboard, no versionado).

## ADR-029 — Gestión: API administrativa web detrás de Cloudflare Access

**Estado:** Aceptado (PO, 2026-09-28). Fase 1 (solo lectura) implementada;
**no desplegada**. La configuración de Access, el hostname y el Tunnel son un
gate humano.

**Fecha:** 2026-09-28

**Contexto:** la app de Gestión (`gestion.artesanfc.com`) existía como
prototipo de interfaz sin backend: guardaba todo en `localStorage` y su login
era simulado (Brain B-026). Para dar de alta contenido real hace falta una API
administrativa, que ADR-026 había dejado fuera (todo por CLI).

**Decisión:**
- API administrativa web **para contenido** (artesanos, piezas y, más adelante,
  media) en **`/api/admin/v1`**. **Certificados y NFC siguen siendo solo por
  CLI** (ADR-026): la API los muestra en solo lectura y nunca expone
  `token_hash`, el token, `physical_uid` ni `storage_path`.
- **Identidad: Cloudflare Access**, delante del hostname de Gestión. La API
  **no confía** en que la petición pasó por Access: verifica ella misma el JWT
  de la cabecera `Cf-Access-Jwt-Assertion` (RS256 contra las claves públicas
  del equipo, audiencia, emisor, expiración) y exige que el email esté en su
  propia allowlist (`ADMIN_EMAILS`). Un proceso del host que llegue a
  `127.0.0.1:8000` sin token recibe 401. No se lee la cookie `CF_Authorization`.
- Sin configuración (`ADMIN_ACCESS_TEAM_DOMAIN`, `ADMIN_ACCESS_AUD`,
  `ADMIN_EMAILS`), todo GET de `/api/admin/*` responde el mismo 404 que una ruta
  inexistente. Una configuración parcial impide arrancar.
- **Namespace `/api/admin/v1`, no `/api/v1/admin`** (API_CONTRACT §14): la regla
  A de Cloudflare permite `/api/v1/*` en `api.artesanfc.com`, y el admin no debe
  quedar dentro de ese prefijo público.
- `audit_event` (DATA_MODEL §2.6) se crea ahora, **append-only en la base**:
  triggers rechazan UPDATE, DELETE y TRUNCATE. Se añade `actor_email`, porque
  Access identifica por email. Cada escritura de las fases siguientes insertará
  su evento en la misma transacción.
- Dependencias nuevas autorizadas por el PO: `pyjwt` y `cryptography` (más
  `cffi` y `pycparser`, transitivas), fijadas con hash en el lock.
- La UI del prototipo se incorpora al repo (`admin/`) y solo cambia su capa de
  datos.

**Alternativas descartadas:** confiar solo en Access (un error de
configuración dejaría el admin abierto, y cualquier proceso del host podría
llamarlo); usuarios y contraseñas propios (más código y más superficie);
admin bajo `/api/v1/admin` (quedaría dentro del prefijo público de la regla A).

**Consecuencias:** fases 2 y 3 (escrituras de artesano y pieza, con auditoría);
fase 4 (media), que depende de la capa de media. El despliegue requiere una
aplicación de Access, su AUD y el team domain en `shared/.env`, y un hostname en
el Tunnel (paso humano).
