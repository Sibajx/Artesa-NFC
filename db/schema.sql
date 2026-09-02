-- ============================================================
-- ARTESA NFC — Esquema de base de datos (Cloudflare D1 / SQLite)
-- Fase 1: Diseño de datos
-- ============================================================
-- Este esquema define dos entidades principales:
--   1. artesanas  -> quién elabora las piezas
--   2. piezas     -> cada prenda/artesanía certificada individualmente
--
-- Se separan en dos tablas para no repetir los datos de la artesana
-- en cada pieza que ella elabore (una artesana puede tener muchas piezas).
-- ============================================================

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- TABLA: artesanas
-- ------------------------------------------------------------
CREATE TABLE artesanas (
  id              TEXT PRIMARY KEY,               -- slug único, ej. 'madeira-sibaja-castillejos'
  nombre          TEXT NOT NULL,
  localidad       TEXT NOT NULL,                  -- ej. 'El Espinal'
  municipio       TEXT NOT NULL,                  -- ej. 'Juchitán'
  estado          TEXT NOT NULL DEFAULT 'Oaxaca',
  lengua          TEXT,                           -- ej. 'Zapoteco del Istmo'
  bio             TEXT,                           -- reseña breve de la artesana
  foto_url        TEXT,
  comercio_justo  INTEGER NOT NULL DEFAULT 1,      -- booleano: 1 = sí, 0 = no
  fecha_registro  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- TABLA: piezas
-- ------------------------------------------------------------
CREATE TABLE piezas (
  id                    TEXT PRIMARY KEY,          -- ID público del certificado, ej. 'IST-2026-HUI-0001'
  artesana_id           TEXT NOT NULL REFERENCES artesanas(id),
  nombre_prenda         TEXT NOT NULL,             -- ej. 'Huipil de Gala'
  tipo                  TEXT NOT NULL,             -- 'huipil' | 'vestido' | 'guayabera' | 'otro'
  tecnica               TEXT,                      -- ej. 'Bordado a mano sobre terciopelo'
  tela                  TEXT,
  colores               TEXT,                      -- JSON: '["#E8621A","#D91F7A","#28B428"]'
  horas_bordado         INTEGER,
  semanas_trabajo       INTEGER,
  hecho_a_mano          INTEGER NOT NULL DEFAULT 1,
  pieza_unica           INTEGER NOT NULL DEFAULT 1,
  historia              TEXT,                      -- texto descriptivo de la pieza
  foto_principal_url    TEXT,
  fotos_adicionales     TEXT,                      -- JSON: array de URLs adicionales
  chip_uid              TEXT UNIQUE,               -- identificador físico del chip NFC (se llena al programarlo)
  url_certificado       TEXT,                      -- ej. 'https://cert.artesanfc.com/IST-2026-HUI-0001'
  estado_certificado    TEXT NOT NULL DEFAULT 'activo', -- 'activo' | 'revocado'
  fecha_creacion_pieza  TEXT,                      -- cuándo se terminó de elaborar la pieza
  fecha_registro        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_piezas_artesana ON piezas(artesana_id);
CREATE INDEX idx_piezas_chip_uid ON piezas(chip_uid);
CREATE INDEX idx_piezas_estado ON piezas(estado_certificado);

-- ------------------------------------------------------------
-- NOTA PARA FASES FUTURAS
-- ------------------------------------------------------------
-- Se puede añadir una tabla `escaneos` para registrar cada vez que
-- se escanea un certificado (fecha, ubicación aproximada, dispositivo)
-- y así generar estadísticas de autenticidad y trazabilidad. No se
-- crea todavía porque corresponde a una fase posterior del proyecto.
