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
