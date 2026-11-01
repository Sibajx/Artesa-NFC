#!/usr/bin/env node
// Llena las regiones <!-- live:* --> de galeria/index.html con lo publicado
// hoy en Gestión (API pública). El resultado es la copia estática que ven los
// buscadores y quien abre la página sin JS; en el navegador, app.js la vuelve
// a pedir en vivo.
//
//   node galeria/scripts/prerender.mjs            # escribe index.html
//   node galeria/scripts/prerender.mjs --check    # solo dice si cambiaría

import { readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  API_BASE,
  piecesWithPhoto,
  renderArtisans,
  renderChapters,
  renderHeroPicture,
  renderWall,
  signature,
} from "../assets/js/render.js";

const here = dirname(fileURLToPath(import.meta.url));
const indexPath = join(here, "..", "index.html");
const check = process.argv.includes("--check");

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json", Origin: "https://artesanfc.com" },
    signal: AbortSignal.timeout(20000),
  });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

async function listAll(path) {
  const out = [];
  let cursor = null;
  for (let page = 0; page < 20; page += 1) {
    const body = await getJson(cursor ? `${path}?cursor=${encodeURIComponent(cursor)}` : path);
    out.push(...(body.data || []));
    cursor = body.meta?.next_cursor;
    if (!cursor) break;
  }
  return out;
}

const CONTAINERS = {
  hero: "data-hero-media",
  wall: 'data-live="wall"',
  chapters: 'data-live="chapters"',
  makers: 'data-live="makers"',
};

function fill(html, name, content) {
  const re = new RegExp(`(<!-- live:${name} -->)[\\s\\S]*?(\\s*<!-- /live:${name} -->)`);
  if (!re.test(html)) throw new Error(`index.html no tiene la región live:${name}`);
  const out = html.replace(re, (_, open, close) => `${open}\n${content.trim()}${close}`);
  // Firma en el contenedor: app.js no repinta si lo que llega es igual.
  const attr = CONTAINERS[name];
  const tag = new RegExp(`(${attr.replace(/"/g, '\\"')})(?: data-sig="[^"]*")?`);
  return out.replace(tag, `$1 data-sig="${signature(content)}"`);
}

const [pieces, artisanList, siteImages] = await Promise.all([
  listAll("/pieces"),
  listAll("/artisans"),
  getJson("/site-images"),
]);
const details = await Promise.all(piecesWithPhoto(pieces).map((p) => getJson(`/pieces/${p.slug}`)));
const artisans = await Promise.all(artisanList.map((a) => getJson(`/artisans/${a.slug}`)));

let html = await readFile(indexPath, "utf8");
const before = html;
const hero = renderHeroPicture(siteImages.data);
if (hero) html = fill(html, "hero", hero);
html = fill(html, "wall", renderWall(details));
html = fill(html, "chapters", renderChapters(details));
const { lead, tiles } = renderArtisans(artisans);
html = fill(html, "makers", lead + tiles);

const summary = `${details.length} piezas con foto (de ${pieces.length} publicadas), ${artisans.length} artesanos`;
if (check) {
  console.log(html === before ? `Sin cambios: ${summary}.` : `Cambiaría index.html: ${summary}.`);
  process.exit(html === before ? 0 : 1);
}
await writeFile(indexPath, html);
console.log(`index.html actualizado: ${summary}.`);
