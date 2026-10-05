# ArtesaNFC — Media (fotos, video y modelos 3D) en el servidor

**Estado:** Aprobado por el PO el 2026-09-29 (M1–M5, ver §8) e implementado en la fase 4 de Gestión (API_CONTRACT §14.3). **El registro se hace desde Gestión, no con una CLI** (§6).
**Fecha:** 2026-09-28
**Relación:** cierra el diseño de PEND-033 / P-019 ("definir la capa de medios"); `docs/API_CONTRACT.md` §6 y §6.1; `docs/DEPLOYMENT.md`; `docs/OPERATIONS.md` (regla A).

## 1. Estado actual

La fase 4 de Gestión implementa la carga, procesamiento y registro de media.
Cuando `MEDIA_ROOT` está configurado, FastAPI sirve únicamente los derivados de
`publico/` bajo `/media/`; sin esa variable, `/media/` responde 404 y las cargas
administrativas responden 503. La regla A de Cloudflare fue ampliada y
verificada externamente el 2026-09-30 para permitir solo `GET|HEAD /media/*`
(`OPERATIONS.md` §8).

Los originales permanecen privados. `artesa-backup` contiene el flujo M3 para
copiarlos cifrados fuera del host cuando existen `MEDIA_ROOT` y `remote.env`
(`BACKUP.md` §16), pero el repositorio no registra su activación ni una
restauración M3. Tampoco contiene ni prueba qué archivos reales están cargados.

Decisión del PO (2026-09-28): las fotos viven **en el servidor actual**, en carpetas por artesano y por pieza.

## 2. Estructura de carpetas

Fuera de `releases/` (un despliegue nunca las toca) y fuera de `shared/` (que es solo estado y secretos):

```text
/home/energias/artesa-nfc/media/
├── originales/                      0700 · PRIVADO · nunca se sirve · soporte M3
│   └── {artesano-slug}/
│       ├── _artesano/               retrato, taller, entrevista (tal como llegan)
│       └── {pieza-slug}/            fotos de cámara, clips, capturas 3D (tal como llegan)
│
└── publico/                         0755 · lo ÚNICO que se sirve en /media/
    ├── artesanos/{artesano-slug}/
    │   ├── portrait-01.jpg
    │   └── process-01.jpg
    └── piezas/{pieza-slug}/
    │   ├── hero-01.jpg
    │   ├── gallery-01.jpg · gallery-02.jpg …
    │   ├── detail-01.jpg
    │   ├── process-01.jpg
    │   └── model-01.glb
```

Los assets de la home se preparan con `web/scripts/prepare-site-media.sh` y se
publican con el build de `web/`; la carga de Gestión no acepta un owner `sitio`.

`storage_path` en la base de datos = ruta relativa a `publico/`, p. ej. `piezas/mascara-cuilapam-01/hero-01.jpg` → URL `https://api.artesanfc.com/media/piezas/mascara-cuilapam-01/hero-01.jpg`.

### Reglas de nombres

| Regla | Por qué |
|---|---|
| Carpeta = **slug** del artesano o pieza (el mismo de la base y de la URL pública) | Estable, sin acentos ni espacios, ya es público; un nombre cambia o se repite |
| Archivo = **rol + número** (`hero`, `gallery`, `detail`, `process`, `portrait`, `model`) | Son los roles de `media_asset.role`; el nombre de la pieza ya lo da la carpeta |
| Minúsculas, `a-z 0-9 -`, sin espacios ni acentos | URLs limpias; nada que codificar |
| **Nunca sobrescribir** un archivo publicado: una foto nueva es `hero-02.jpg` | Cloudflare y los navegadores cachean `/media/` de forma inmutable (§4) |
| Borrar solo lo que **nunca fue público**; al borrar queda `hero-01.deleted` (vacío) | Una foto subida por error se elimina por completo, pero su número no se reutiliza: una URL ya cacheada nunca apunta a otra foto. Lo que pudo verse solo se archiva (2026-10) |
| Las piezas van en `piezas/`, no dentro del artesano | Una pieza pertenece a un artesano en el MVP (ADR-004), pero su URL no debe romperse si eso cambia |

En `originales/` sí se agrupa por artesano (como lo propuso el PO): es el archivo de trabajo humano.

## 3. Originales ≠ públicos (privacidad)

Las fotos de celular llevan metadatos **EXIF**, entre ellos **la ubicación GPS** (que puede revelar la casa o el taller del artesano), el modelo del teléfono y la fecha. Por eso:

- `originales/` **nunca** se sirve y solo lo lee el operador.
- A `publico/` solo llegan **derivados procesados**: EXIF eliminado (incluido GPS), rotación aplicada, lado mayor ≤ 1600 px, JPEG progresivo calidad ~80 (≈150–350 KB). Más adelante: variantes AVIF/WebP y tamaños (ver §7).
- Videos: sin pista de audio y sin metadatos de ubicación; ≤ 4 MB por archivo.
- GLB: 2–8 MB, texturas 1024–2048 px.

El backend de Gestión realiza el procesado (§6); no depende de una edición a
mano.

## 4. Cómo se sirve — implementación del piloto

**Implementado: la propia API sirve `/media/` desde `publico/`.**

```text
navegador ─► api.artesanfc.com/media/…  (Cloudflare, caché en el borde)
          ─► Tunnel ─► Uvicorn/FastAPI  (MEDIA_ROOT/publico)
```

- `web/` y `frontend/` resuelven `/media/…` contra el origen de la API.
- `MEDIA_ROOT` apunta al directorio que contiene `originales/` y `publico/`.
- `backend/app/core/media_files.py` permite los formatos aprobados, no lista
  directorios, bloquea traversal, aplica caché inmutable y nunca sirve
  `originales/`.
- La regla A permite `GET` y `HEAD` en `/media/*`; el resto del host conserva la
  allowlist de `OPERATIONS.md` §8.
- Carga: tras el primer acceso, Cloudflare sirve desde su caché; Python casi no interviene.

**Opción B (evolución):** `media.artesanfc.com` con un servidor estático propio (Caddy) detrás del mismo Tunnel, o Cloudflare R2. Más limpio a escala, pero más infraestructura; el backend solo cambiaría cómo arma `url` (cambio no disruptivo, §13). No hace falta para el piloto.

## 5. Backups **[PO]**

**Código implementado (M3, 2026-09-30):** `artesa-backup` puede copiar
`originales/` fuera del host, cifrado con K1+K2, de forma incremental y
direccionada por contenido. El índice va dentro del bundle cifrado de la base.
Ver `docs/BACKUP.md` §16; la restauración se hace con
`qa/d10-offhost-drill/media_restore.py`. No hay un registro versionado de
activación, primer run o restore sample de M3.


- `originales/` es material irremplazable y puede entrar en el backup fuera del
  host mediante `artesa-backup` cuando `MEDIA_ROOT`, `remote.env` y el flujo M3
  están activos; no forma parte de `pg_dump`.
- `publico/` no se respalda. El repo tampoco contiene un procedimiento
  determinista que reconstruya las rutas ya referenciadas por la base; recuperar
  solo DB + originales requiere reconciliar o volver a registrar derivados.
- Tamaño estimado del piloto: < 2 GB.

## 6. Registro en la base de datos

**Decisión del PO (2026-09-29), implementada:** la subida se hace desde
**Gestión** (`gestion.artesanfc.com`). El servidor procesa el archivo, guarda el
original y registra el `media_asset` con su `audit_event` (API_CONTRACT §14.3).
El video no se recodifica: se rechaza si trae audio o ubicación; `webm`,
AVIF/WebP publicados y la carpeta `sitio/` quedan fuera de la subida actual.

### 6.1 Historia: propuesta de CLI descartada

Antes de Gestión se propuso una CLI local, al estilo de
`app.cli.provision`:

```bash
python -m app.cli.media add --piece mascara-cuilapam-01 --role hero \
    --file media/originales/{artesano}/{pieza}/IMG_2044.jpg \
    --alt "Máscara de madera tallada vista de frente, con pintura roja y negra"
```

1. valida slug, rol y que `--alt` no esté vacío (obligatorio para imágenes, `DATA_MODEL.md`);
2. procesa el original (EXIF fuera, rotación, tamaño, formato) → `publico/piezas/{slug}/{rol}-{n}.jpg` (nunca sobrescribe);
3. inserta el `media_asset` con `storage_path`, `position`, `format` (ancho/alto/mime o tamaño del GLB) en una transacción;
4. `--dry-run` obligatorio primero; `list`, `archive` (retira sin borrar).

Esta propuesta se conserva como historia y no describe la interfaz vigente.

## 7. Después del piloto

- Variantes responsive (`hero-01-800.avif`, …) con una clave aditiva en `format` o en `MEDIA_ASSET` (§13 no disruptivo).
- Mover `publico/` a R2 o a `media.artesanfc.com` si el tráfico lo pide (Opción B).

## 8. Decisiones **[PO]** — aprobadas el 2026-09-29 tal como se recomiendan, salvo M4 (subida desde Gestión)

| # | Decisión | Recomendación |
|---|---|---|
| M1 | Ruta base en el servidor | `/home/energias/artesa-nfc/media/{originales,publico}` |
| M2 | Cómo se sirve | Opción A (API + regla A ampliada a `GET /media/*`) |
| M3 | Backup de `originales/` | Job cifrado fuera del host junto a D10.2 |
| M4 | Herramienta de registro | Gestión (`admin/` + API administrativa), decisión que reemplazó la propuesta de CLI |
| M5 | Tamaño máximo publicado | Fotos ≤ 1600 px; video ≤ 4 MB; GLB ≤ 8 MB |
