// Galería artesanfc.com — de los datos públicos de Gestión a HTML.
//
// Este módulo no toca el DOM ni la red: recibe lo que devuelve la API pública
// (api.artesanfc.com/api/v1) y devuelve cadenas HTML. Lo usan el navegador
// (app.js, datos en vivo) y Node (scripts/prerender.mjs, la copia estática que
// se publica dentro de index.html para buscadores y para quien no tiene JS).
//
// Todo texto que viene de la API pasa por esc(); toda URL de media pasa por
// mediaUrl() y todo color por color(). Nada de la API entra crudo al HTML.

import { FOTOS } from "./fotos.js";

export const API_ORIGIN = "https://api.artesanfc.com";
export const API_BASE = `${API_ORIGIN}/api/v1`;
export const INFO_ORIGIN = "https://info.artesanfc.com";

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

// Solo rutas /media/... del origen de la API (API_CONTRACT §6.1).
export function mediaUrl(path) {
  if (typeof path !== "string" || !path.startsWith("/media/") || path.includes("..")) return null;
  return API_ORIGIN + path.split("/").map((p) => encodeURIComponent(decodeURIComponent(p))).join("/");
}

export function color(value) {
  return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value) ? value.toLowerCase() : null;
}

function slugOk(slug) {
  return typeof slug === "string" && /^[a-z0-9][a-z0-9-]{0,120}$/.test(slug);
}

export function pieceUrl(slug) {
  return slugOk(slug) ? `${INFO_ORIGIN}/piezas/${slug}/` : `${INFO_ORIGIN}/piezas/`;
}

export function artisanUrl(slug) {
  return slugOk(slug) ? `${INFO_ORIGIN}/artesanos/${slug}/` : `${INFO_ORIGIN}/artesanos/`;
}

/* ---------- color ---------- */

function rgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function luminance(hex) {
  const [r, g, b] = rgb(hex).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function hsv(hex) {
  const [r, g, b] = rgb(hex).map((v) => v / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h = (h * 60 + 360) % 360;
  }
  return { h, s: max ? d / max : 0, v: max };
}

export const INK_DARK = "#0d0b0a"; // noche
export const INK_LIGHT = "#efe3cc"; // crema alzada: nunca blanco puro
const WOOD = "#5c3f28";

// Entona un color hacia la madera (como un tinte natural sobre fibra): el
// rojo de una paleta deja de ser rojo de pantalla y se vuelve grana.
export function toWood(hex, amount = 0.28) {
  const c = color(hex);
  if (!c) return hex;
  const a = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
  const b = [1, 3, 5].map((i) => parseInt(WOOD.slice(i, i + 2), 16));
  return "#" + a.map((v, i) => Math.round(v * (1 - amount) + b[i] * amount).toString(16).padStart(2, "0")).join("");
}

export function inkFor(field) {
  return contrast(field, INK_DARK) >= contrast(field, INK_LIGHT) ? INK_DARK : INK_LIGHT;
}

// El capítulo de cada pieza toma el color más vivo de su propia paleta
// (visual_theme.palette, la que se ve en Gestión). Si se parece demasiado al
// capítulo anterior, toma el siguiente; si la paleta no sirve, usa la tierra.
export function chapterColours(palette, previousField) {
  const colours = (Array.isArray(palette) ? palette : []).map(color).filter(Boolean);
  const ranked = colours
    .map((c) => ({ c, ...hsv(c) }))
    .filter((x) => x.s > 0.25 && x.v > 0.3)
    .sort((a, b) => b.s * b.v - a.s * a.v);
  const prevHue = previousField ? hsv(previousField).h : null;
  const pick =
    ranked.find((x) => prevHue === null || Math.min(Math.abs(x.h - prevHue), 360 - Math.abs(x.h - prevHue)) > 28) ||
    null;
  // `source` es el color sin entonar: el siguiente capítulo compara su tono
  // con este, no con el entonado (que tira todo hacia el naranja de la madera).
  const source = pick ? pick.c : previousField === "#2c1a12" ? "#5b3a24" : "#2c1a12";
  const field = toWood(source);
  const ink = inkFor(field);
  const darkest = colours.slice().sort((a, b) => luminance(a) - luminance(b))[0];
  const detail = darkest && contrast(field, darkest) >= 3 && darkest !== field ? darkest : ink;
  return { field, ink, detail, source };
}

/* ---------- datos ---------- */

export function piecesWithPhoto(list) {
  return (Array.isArray(list) ? list : []).filter((p) => p && slugOk(p.slug) && mediaUrl(p.cover_media?.url));
}

function firstParagraph(text, max = 420) {
  const para = String(text ?? "")
    .split(/\n\s*\n/)
    .map((s) => s.trim())
    .find(Boolean);
  if (!para) return "";
  if (para.length <= max) return para;
  const cut = para.slice(0, max);
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("; "));
  return (end > max * 0.5 ? cut.slice(0, end + 1) : cut.replace(/\s+\S*$/, "") + "…").trim();
}

export function cleanName(name) {
  return String(name ?? "").replace(/^["“”']+|["“”']+$/g, "").trim();
}

export function artisanName(a) {
  if (!a) return "";
  return a.artistic_name ? `${a.full_name} (${a.artistic_name})` : a.full_name || "";
}

const AVAILABILITY = {
  available: "Disponible",
  exhibited: "En exhibición",
  reserved: "Apartada",
  sold: "Vendida",
};

function img(media, { alt, sizes = "100vw", eager = false, cls = "" } = {}) {
  const src = mediaUrl(media?.url);
  if (!src) return "";
  const w = Number(media.format?.width) || 1200;
  const h = Number(media.format?.height) || 1600;
  // Copias WebP reducidas que deja scripts/fotos.py al publicar (solo rutas
  // propias assets/fotos/…); la original queda como la opción más grande.
  const copies = (FOTOS[media.url] || []).filter(
    ([cw, u]) => Number.isInteger(cw) && cw < w && /^assets\/fotos\/[a-f0-9]{12}-\d+\.webp$/.test(u),
  );
  const srcset = copies.length ? ` srcset="${esc([...copies.map(([cw, u]) => `${u} ${cw}w`), `${src} ${w}w`].join(", "))}"` : "";
  return `<img${cls ? ` class="${cls}"` : ""} src="${esc(src)}"${srcset} alt="${esc(alt ?? media.alt_text ?? "")}" width="${w}" height="${h}" sizes="${esc(sizes)}" decoding="async"${eager ? ' fetchpriority="high"' : ' loading="lazy"'}>`;
}

/* ---------- secciones ---------- */

// Muro de piezas: por cada pieza, su foto, su nombre en una losa de su color
// y una losa con su madera o su origen. Cierra con el total.
export function renderWall(details) {
  if (!details.length) {
    return `<p class="wall__empty">Muy pronto: las primeras piezas se están fotografiando.</p>`;
  }
  const tiles = [];
  const facts = [];
  const seen = new Set();
  const addFact = (word, label) => {
    const w = String(word ?? "").trim();
    const key = w.toLowerCase();
    if (!w || seen.has(key)) return;
    seen.add(key);
    facts.push([w, label]);
  };
  let prev = null;
  details.forEach((p) => {
    const { field, ink, source } = chapterColours(p.visual_theme?.palette, prev);
    prev = source;
    const href = `#pieza-${esc(p.slug)}`;
    const name = cleanName(p.name);
    const cover = p.media?.[0] ?? p.cover_media;
    tiles.push(
      `<a class="tile tile--photo" href="${href}" aria-label="${esc(name)}" style="--tile:${field}">${img(cover, { alt: "", sizes: "(min-width: 760px) 25vw, 50vw" })}</a>`,
    );
    tiles.push(
      `<a class="tile tile--word" href="${href}" style="--tile:${field};--tile-ink:${ink}"><span class="tile__word">${esc(name)}</span><span class="tile__cap">${esc(p.public_code)}</span></a>`,
    );
    (p.materials || []).forEach((m) => {
      const isWood = /^madera\b/i.test(m) || /zompantle|cedro|caoba|ahuehuete|copal/i.test(m);
      addFact(String(m).replace(/^madera de\s+/i, ""), isWood ? "Madera" : "Material");
    });
    addFact(p.origin, "Origen");
  });
  // El muro siempre cierra filas completas de cuatro (y de dos en el teléfono).
  const n = details.length;
  const fallback = [
    ["A mano", "Sin moldes"],
    ["NFC", "Un chip en cada pieza"],
    ["Oaxaca", "De donde vienen"],
  ];
  const need = (4 - ((tiles.length + 1) % 4)) % 4;
  const pool = facts.concat(fallback);
  pool.slice(0, need).forEach(([word, label]) => {
    tiles.push(
      `<div class="tile tile--fact"><span class="tile__word tile__word--small">${esc(word)}</span><span class="tile__cap">${esc(label)}</span></div>`,
    );
  });
  tiles.push(
    `<a class="tile tile--count" href="${INFO_ORIGIN}/piezas/"><span class="tile__word">${n}</span><span class="tile__cap">${n === 1 ? "pieza certificada" : "piezas certificadas"} · ver el catálogo</span></a>`,
  );
  return `<div class="wall__grid">${tiles.join("")}</div>`;
}

// Un capítulo de color por pieza.
export function renderChapters(details) {
  let prev = null;
  return details
    .map((p, i) => {
      const { field, ink, detail, source } = chapterColours(p.visual_theme?.palette, prev);
      prev = source;
      const name = cleanName(p.name);
      const cover = p.media?.[0] ?? p.cover_media;
      const facts = [
        ["Quién la talló", p.artisan ? `<a href="${artisanUrl(p.artisan.slug)}">${esc(artisanName(p.artisan))}</a>` : ""],
        ["Material", esc((p.materials || []).join(", "))],
        ["Técnica", esc(p.technique)],
        ["Origen", esc(p.origin)],
        ["Año", esc(p.creation_year)],
        ["Estado", esc(AVAILABILITY[p.availability_status] || "")],
        ["Código", esc(p.public_code)],
      ].filter(([, v]) => v);
      const story = firstParagraph(p.description) || firstParagraph(p.history);
      return `<section class="chapter piece${i % 2 ? " piece--flip" : ""}" id="pieza-${esc(p.slug)}" data-field style="--field:${field};--ink:${ink};--detail:${detail}" aria-labelledby="pieza-${esc(p.slug)}-t">
  <div class="piece__media" data-reveal>${img(cover, { sizes: "(min-width: 900px) 45vw, 100vw" })}</div>
  <div class="piece__text">
    <h2 class="piece__name" id="pieza-${esc(p.slug)}-t" data-reveal>${esc(name)}</h2>
    ${story ? `<p class="piece__story" data-reveal>${esc(story)}</p>` : ""}
    <dl class="piece__facts" data-reveal>${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
    <a class="button button--ink" href="${pieceUrl(p.slug)}" data-reveal>Ver su certificado y su historia</a>
  </div>
</section>`;
    })
    .join("\n");
}

// Quién las talla: retrato si lo hay; si no, su primera pieza.
export function renderArtisans(artisans) {
  const withImage = artisans
    .map((a) => {
      const portrait = (a.media || []).find((m) => m.role === "portrait" && mediaUrl(m.url));
      const pieces = (a.pieces || []).filter((p) => mediaUrl(p.cover_media?.url));
      const picture = portrait || pieces[0]?.cover_media;
      return picture ? { a, picture, pieces } : null;
    })
    .filter(Boolean)
    .sort((x, y) => y.pieces.length - x.pieces.length);
  if (!withImage.length) return { lead: "", tiles: "" };
  const lead = withImage[0];
  const place = [lead.a.location?.locality, lead.a.location?.state].filter(Boolean).join(", ");
  const leadHtml = `<div class="maker__lead">
    <p class="maker__name" data-reveal><a href="${artisanUrl(lead.a.slug)}">${esc(artisanName(lead.a))}</a>${place ? `<span>${esc(place)}</span>` : ""}</p>
    <p class="maker__bio" data-reveal>${esc(firstParagraph(lead.a.biography, 520))}</p>
    ${lead.a.history ? `<p class="maker__bio" data-reveal>${esc(firstParagraph(lead.a.history, 360))}</p>` : ""}
  </div>`;
  const tiles = withImage
    .map(({ a, picture, pieces }) => {
      const n = pieces.length;
      const role = [a.location?.locality, n ? `${n} ${n === 1 ? "pieza" : "piezas"}` : ""].filter(Boolean).join(" · ");
      return `<a class="maker" href="${artisanUrl(a.slug)}" data-reveal>
      <span class="maker__frame">${img(picture, { alt: `${artisanName(a)}`, sizes: "(min-width: 760px) 22vw, 45vw" })}</span>
      <span class="maker__who">${esc(artisanName(a))}</span>
      ${role ? `<span class="maker__role">${esc(role)}</span>` : ""}
    </a>`;
    })
    .join("");
  return { lead: leadHtml, tiles: `<div class="makers">${tiles}</div>` };
}

export function renderHeroPicture(siteImages) {
  const entry = siteImages?.["collection-entry"];
  const jpg = mediaUrl(entry?.jpg);
  if (!jpg) return null;
  const avif = mediaUrl(entry.avif);
  const webp = mediaUrl(entry.webp);
  return `<picture>${avif ? `<source type="image/avif" srcset="${esc(avif)}">` : ""}${webp ? `<source type="image/webp" srcset="${esc(webp)}">` : ""}<img class="hero__img" src="${esc(jpg)}" alt="Las máscaras de El Negrito y El Viejito, talladas en zompantle, sobre musgo" width="${Number(entry.width) || 914}" height="${Number(entry.height) || 1142}" fetchpriority="high" decoding="async"></picture>`;
}

// Resultado del verificador de códigos.
export function renderMatch(piece) {
  if (!piece) return "";
  const name = cleanName(piece.name);
  return `<a class="match" href="${pieceUrl(piece.slug)}">
    <span class="match__img">${img(piece.cover_media, { sizes: "120px" })}</span>
    <span class="match__text"><strong>${esc(name)}</strong><span>${esc(piece.public_code)} · ${esc(AVAILABILITY[piece.availability_status] || "Registrada")}</span><span class="match__go">Abrir su certificado</span></span>
  </a>`;
}

export function normaliseCode(input) {
  const raw = String(input ?? "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (!raw) return "";
  const body = raw.startsWith("ANFC") ? raw.slice(4) : raw;
  return body ? `ANFC-${body}` : "";
}

// Firma corta de un bloque renderizado: si no cambia, no se toca el DOM.
export function signature(html) {
  let h = 2166136261;
  for (let i = 0; i < html.length; i += 1) {
    h ^= html.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(36);
}
