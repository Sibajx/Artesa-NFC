# ArtesaNFC — Gestión (`admin/`)

Consola interna para administrar contenido (docs/DECISIONS.md **ADR-029**). React
19 + Vite + Tailwind 4, con el diseño "Esencia Botánica" del prototipo original
del equipo.

**Fases 1 a 4.** Resumen, artesanos y piezas: alta, edición, publicar, pasar a
borrador, archivar, restaurar y disponibilidad. También auditoría. Fase 4: subir
fotos, videos y modelos 3D, editar su descripción y orden, y archivarlos
(docs/MEDIA.md). Las vistas previas se cargan desde `https://api.artesanfc.com/media/`
(`VITE_MEDIA_ORIGIN` lo cambia; `npm run dev` reenvía `/media` al backend local). **Certificados y etiquetas NFC nunca se gestionan desde
aquí**: siguen en la CLI de provisioning (ADR-026).

## Cómo funciona

```text
navegador ── https://gestion.artesanfc.com ── Cloudflare Access (login por email)
                    │
                    ▼  Tunnel (cloudflared, reglas por ruta)
        /api/admin/*  → 127.0.0.1:8000  (FastAPI, verifica el JWT de Access + ADMIN_EMAILS)
        todo lo demás → 127.0.0.1:8003  (archivos estáticos de admin/dist)
```

- **Mismo origen.** La UI y la API comparten el hostname, así que no hay CORS ni
  cookies entre dominios. Access protege el hostname completo.
- **Sin login propio.** Access autentica. La UI solo llama a `/api/admin/v1/me`:
  un 401 significa que la sesión expiró (se ofrece recargar) y un 403 que el email
  no está en `ADMIN_EMAILS`. Para cerrar sesión: `/cdn-cgi/access/logout`.
- **Rutas con `#`** (`#/piezas/<id>`): el servidor de estáticos no necesita
  redirigir rutas profundas a `index.html`.
- **La UI no guarda nada** en `localStorage` ni maneja tokens.
- **Escrituras:** cada una lleva `X-Artesa-Admin: 1`, JSON y `If-Match` con el
  `updated_at` que se ve en pantalla. Si otra persona cambió el registro, la API
  responde 412 y la UI pide recargar. Los errores se muestran en español a
  partir del código; el texto del servidor nunca se muestra.

## Desarrollo

```bash
cd admin
npm ci
npm run dev        # http://localhost:5173; /api/admin se reenvía a 127.0.0.1:8000
npm run lint && npm run typecheck && npm run build
```

Sin un token de Access, el backend local responde 404 (admin sin configurar) o
401. Para revisar pantallas sin backend, sirve `dist/` con un stub que responda
`/api/admin/v1/*` con el contrato de `docs/API_CONTRACT.md` §14.1.

## Despliegue y verificación (gate humano)

El repositorio documenta fase 1 desplegada; no permite verificar el estado vivo
de los despliegues posteriores. Esta lista sirve para activar o auditar Gestión.

1. **Backend:** un release que incluya ADR-029, desplegado con
   `--allow-migration` (crea `audit_event`).
2. **Cloudflare Zero Trust:**
   - crear una aplicación *self-hosted* para `gestion.artesanfc.com`, con una
     política *Allow* para los emails autorizados;
   - anotar el **AUD tag** y el **team domain**.
3. **`shared/.env`**: `ADMIN_ACCESS_TEAM_DOMAIN`, `ADMIN_ACCESS_AUD` y
   `ADMIN_EMAILS` (los tres, o ninguno). Después, reiniciar el servicio.
4. **UI:**
   - `npm ci && npm run build`, con el mismo commit que `main`;
   - copiar `dist/` a `/home/energias/artesa-nfc/shared/admin-ui/<commit>/`;
   - apuntar el symlink `shared/admin-ui/current` a ese directorio;
   - instalar `deploy/artesa-admin-ui.service.example`, que sirve `current` en
     `127.0.0.1:8003`.
5. **Tunnel:** en la configuración de `cloudflared`, antes de la regla de
   `api.artesanfc.com`:
   ```yaml
   - hostname: gestion.artesanfc.com
     path: ^/api/admin/
     service: http://127.0.0.1:8000
   - hostname: gestion.artesanfc.com
     service: http://127.0.0.1:8003
   ```
   Añadir además el DNS de `gestion` al Tunnel.
6. **Verificar:**
   - sin sesión, Access redirige al login;
   - con un email autorizado, se ve el Resumen;
   - con otro email, aparece "Sin acceso";
   - `https://api.artesanfc.com/api/admin/v1/me` sigue bloqueado por la regla A.

### Fase 4: medios (activación o auditoría)

1. Carpetas en el servidor:
   ```bash
   install -d -m 0700 /home/energias/artesa-nfc/media /home/energias/artesa-nfc/media/originales
   install -d -m 0755 /home/energias/artesa-nfc/media/publico
   chmod 0711 /home/energias/artesa-nfc/media
   ```
2. `shared/.env`: `MEDIA_ROOT=/home/energias/artesa-nfc/media`. La herramienta de
   deploy (TOOL 1.6.0) lo acepta y exige que sea absoluto y contenga las dos
   carpetas.
3. Deploy del release (sin migración) e `install-tools`, que pasa de 1.5.0 a 1.6.0.
4. Cloudflare: verificar que la **regla A** permite `GET` y `HEAD` en
   `api.artesanfc.com/media/*`. `docs/OPERATIONS.md` registra esa verificación
   externa el 2026-09-30.
5. Backup: el código de `artesa-backup` incorpora `originales/` al respaldo
   cifrado fuera del host cuando `MEDIA_ROOT` y `remote.env` están configurados.
   `docs/BACKUP.md` §16 describe el flujo, sin registrar su activación o un
   restore sample M3.
