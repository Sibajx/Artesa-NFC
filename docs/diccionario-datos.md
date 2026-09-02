# Diccionario de datos — Artesa NFC

Este documento explica qué significa cada campo del esquema (`db/schema.sql`) y
la convención para generar los IDs de certificado. Sirve como referencia para
cualquier persona que registre piezas o dé mantenimiento al sistema.

## Convención de ID de certificado

Formato: `REGION-AÑO-TIPO-CONSECUTIVO`

Ejemplo: **`IST-2026-HUI-0001`**

| Segmento | Significado | Ejemplos |
|---|---|---|
| `REGION` | Región de Oaxaca donde se elaboró la pieza | `IST` (Istmo de Tehuantepec), `VAL` (Valles Centrales), `MIX` (Mixteca), `SIE` (Sierra) |
| `AÑO` | Año de registro | `2026` |
| `TIPO` | Categoría de la pieza (3 letras) | `HUI` (huipil), `VES` (vestido), `GUA` (guayabera), `REB` (rebozo), `OTR` (otro) |
| `CONSECUTIVO` | Número secuencial de 4 dígitos, reinicia cada año y categoría | `0001`, `0002`... |

Este ID es el que se usa en la URL del certificado, ej.
`artesanfc.com/cert/IST-2026-HUI-0001` o `cert.artesanfc.com/IST-2026-HUI-0001`.

## Tabla `artesanas`

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `id` | TEXT (PK) | Sí | Slug único de la artesana, ej. `madeira-sibaja-castillejos` |
| `nombre` | TEXT | Sí | Nombre completo |
| `localidad` | TEXT | Sí | Pueblo o localidad, ej. `El Espinal` |
| `municipio` | TEXT | Sí | Municipio, ej. `Juchitán` |
| `estado` | TEXT | Sí | Estado (por defecto `Oaxaca`) |
| `lengua` | TEXT | No | Lengua indígena que habla, ej. `Zapoteco del Istmo` |
| `bio` | TEXT | No | Reseña breve de la artesana |
| `foto_url` | TEXT | No | URL de su fotografía |
| `comercio_justo` | INTEGER (0/1) | Sí | Si la pieza se vendió bajo esquema de comercio justo |
| `fecha_registro` | TEXT | Sí (auto) | Fecha en que se dio de alta en el sistema |

## Tabla `piezas`

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `id` | TEXT (PK) | Sí | ID público del certificado (ver convención arriba) |
| `artesana_id` | TEXT (FK) | Sí | Referencia a `artesanas.id` |
| `nombre_prenda` | TEXT | Sí | Nombre comercial, ej. `Huipil de Gala` |
| `tipo` | TEXT | Sí | `huipil`, `vestido`, `guayabera`, `otro` |
| `tecnica` | TEXT | No | Ej. `Bordado a mano sobre terciopelo` |
| `tela` | TEXT | No | Material base |
| `colores` | TEXT (JSON) | No | Array de colores hex usados, ej. `["#E8621A","#D91F7A"]` |
| `horas_bordado` | INTEGER | No | Horas invertidas en el bordado |
| `semanas_trabajo` | INTEGER | No | Semanas de elaboración |
| `hecho_a_mano` | INTEGER (0/1) | Sí | Por defecto `1` |
| `pieza_unica` | INTEGER (0/1) | Sí | Por defecto `1` |
| `historia` | TEXT | No | Texto descriptivo de la pieza para el certificado |
| `foto_principal_url` | TEXT | No | Foto principal de la pieza |
| `fotos_adicionales` | TEXT (JSON) | No | Array de URLs de fotos adicionales |
| `chip_uid` | TEXT (único) | No | Identificador físico del chip NFC grabado |
| `url_certificado` | TEXT | No | URL final del certificado público |
| `estado_certificado` | TEXT | Sí | `activo` o `revocado` |
| `fecha_creacion_pieza` | TEXT | No | Cuándo se terminó de elaborar la prenda |
| `fecha_registro` | TEXT | Sí (auto) | Cuándo se registró en el sistema |

## Por qué se separan `artesanas` y `piezas`

Una artesana suele elaborar varias piezas a lo largo del tiempo. Si sus datos
(nombre, localidad, lengua, biografía) se guardaran repetidos en cada pieza,
cualquier corrección tendría que hacerse pieza por pieza. Al tener una tabla
separada, sus datos se editan una sola vez y todas sus piezas certificadas
se actualizan automáticamente.
