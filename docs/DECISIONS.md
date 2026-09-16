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
