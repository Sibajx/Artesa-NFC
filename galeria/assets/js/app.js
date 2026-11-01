// Galería artesanfc.com — capa viva.
//
// La página ya llega con una copia de Gestión (scripts/prerender.mjs). Aquí se
// vuelve a pedir a la API pública para que lo publicado hoy aparezca sin
// redesplegar, y se montan el verificador de códigos, el acordeón, la barra que
// toma el color de cada capítulo y las entradas al desplazarse.

import {
  API_BASE,
  normaliseCode,
  piecesWithPhoto,
  renderArtisans,
  renderChapters,
  renderHeroPicture,
  renderMatch,
  renderWall,
  signature,
} from "./render.js";

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`, { headers: { Accept: "application/json" }, credentials: "omit" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function allPages(path) {
  const out = [];
  let cursor = null;
  for (let i = 0; i < 20; i += 1) {
    const sep = path.includes("?") ? "&" : "?";
    const page = await getJson(cursor ? `${path}${sep}cursor=${encodeURIComponent(cursor)}` : path);
    out.push(...(page.data || []));
    cursor = page.meta?.next_cursor;
    if (!cursor) break;
  }
  return out;
}

let piecesCache = null;
function loadPieces() {
  piecesCache ??= allPages("/pieces");
  return piecesCache;
}

/* ---------- datos en vivo ---------- */

function replaceRegion(el, html) {
  if (!el || typeof html !== "string") return false;
  const sig = signature(html);
  if (el.dataset.sig === sig) return false;
  el.innerHTML = html;
  el.dataset.sig = sig;
  return true;
}

async function refreshFromGestion() {
  const [pieces, artisans, images] = await Promise.all([
    loadPieces(),
    allPages("/artisans").catch(() => []),
    getJson("/site-images").catch(() => null),
  ]);

  const hero = renderHeroPicture(images?.data);
  if (hero) replaceRegion(document.querySelector("[data-hero-media]"), hero);

  const shown = piecesWithPhoto(pieces);
  const details = (
    await Promise.all(shown.map((p) => getJson(`/pieces/${encodeURIComponent(p.slug)}`).catch(() => null)))
  )
    .filter(Boolean)
    .map((d, i) => ({ ...shown[i], ...d }));

  const wall = document.querySelector('[data-live="wall"]');
  const wallChanged = replaceRegion(wall, renderWall(details));

  const chaptersChanged = replaceRegion(document.querySelector('[data-live="chapters"]'), renderChapters(details));

  const fullArtisans = (
    await Promise.all(artisans.map((a) => getJson(`/artisans/${encodeURIComponent(a.slug)}`).catch(() => null)))
  ).filter(Boolean);
  const makers = renderArtisans(fullArtisans);
  const makersChanged = replaceRegion(document.querySelector('[data-live="makers"]'), makers.lead + makers.tiles);

  if (wallChanged || chaptersChanged || makersChanged) {
    setupReveal();
    setupBar();
  }
}

/* ---------- verificador ---------- */

function setupVerify() {
  const form = document.querySelector("[data-verify]");
  const out = document.querySelector("[data-verify-out]");
  if (!form || !out) return;
  const input = form.querySelector("input");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = normaliseCode(input.value);
    if (!code) {
      out.innerHTML = "<p>Escribe el código tal como viene en la etiqueta, por ejemplo ANFC-6AJA2J.</p>";
      input.focus();
      return;
    }
    input.value = code;
    out.innerHTML = "<p>Buscando…</p>";
    form.classList.add("is-busy");
    try {
      const pieces = await loadPieces();
      const match = pieces.find((p) => normaliseCode(p.public_code) === code);
      out.innerHTML = match
        ? renderMatch(match)
        : `<p><strong>No encontramos ${code}.</strong> Revisa letras parecidas (0 y O, 1 e I). Si la pieza es nueva, puede que aún no esté publicada; escríbenos a <a href="mailto:contacto@artesanfc.com?subject=C%C3%B3digo%20${encodeURIComponent(code)}">contacto@artesanfc.com</a>.</p>`;
    } catch {
      out.innerHTML =
        '<p>No pudimos consultar el registro en este momento. Inténtalo de nuevo en un minuto o abre el <a href="https://info.artesanfc.com/piezas/">catálogo completo</a>.</p>';
    } finally {
      form.classList.remove("is-busy");
    }
  });
}

/* ---------- acordeón ---------- */

function setupStages() {
  const root = document.querySelector("[data-stages]");
  if (!root) return;
  const stages = [...root.querySelectorAll(".stage")];
  stages.forEach((stage) => {
    const button = stage.querySelector("button");
    button.addEventListener("click", () => {
      stages.forEach((s) => {
        const open = s === stage;
        s.classList.toggle("is-open", open);
        s.querySelector("button").setAttribute("aria-expanded", String(open));
        s.querySelector(".stage__body").hidden = !open;
      });
    });
  });
}

/* ---------- barra que toma el color del capítulo ---------- */

let barObserver = null;
function setupBar() {
  const bar = document.querySelector("[data-bar]");
  const hero = document.querySelector(".hero");
  if (!bar) return;
  barObserver?.disconnect();
  const fields = [...document.querySelectorAll("[data-field]")];
  const update = () => {
    const line = bar.offsetHeight / 2;
    const under = fields.find((f) => {
      const r = f.getBoundingClientRect();
      return r.top <= line && r.bottom > line;
    });
    if (under) {
      const cs = getComputedStyle(under);
      bar.style.setProperty("--bar-bg", cs.getPropertyValue("--field").trim());
      bar.style.setProperty("--bar-ink", cs.getPropertyValue("--ink").trim());
    }
    const heroBottom = hero ? hero.getBoundingClientRect().bottom : 0;
    bar.classList.toggle("is-shown", heroBottom < line * 2);
  };
  barObserver = new IntersectionObserver(update, { rootMargin: "0px 0px -90% 0px", threshold: [0, 1] });
  fields.forEach((f) => barObserver.observe(f));
  if (hero) barObserver.observe(hero);
  if (!setupBar.bound) {
    window.addEventListener("scroll", () => requestAnimationFrame(update), { passive: true });
    window.addEventListener("resize", update);
    setupBar.bound = true;
  }
  update();
}

/* ---------- entradas ---------- */

let revealObserver = null;
function setupReveal() {
  const items = [...document.querySelectorAll("[data-reveal]:not(.is-in)")];
  if (reduceMotion || !("IntersectionObserver" in window)) {
    items.forEach((el) => el.classList.add("is-in"));
    return;
  }
  revealObserver ??= new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-in");
        revealObserver.unobserve(entry.target);
      });
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.05 },
  );
  const groups = new Map();
  items.forEach((el) => {
    const key = el.closest("section, footer") || document.body;
    const n = groups.get(key) ?? 0;
    groups.set(key, n + 1);
    el.style.setProperty("--i", String(Math.min(n, 8)));
    revealObserver.observe(el);
  });
}

/* ---------- la foto del inicio se mueve con el scroll ---------- */

function setupHeroDrift() {
  const media = document.querySelector("[data-hero-media]");
  if (!media || reduceMotion) return;
  let ticking = false;
  const move = () => {
    ticking = false;
    const y = Math.min(window.scrollY, window.innerHeight);
    media.style.setProperty("--drift", `${(y * 0.12).toFixed(1)}px`);
  };
  window.addEventListener(
    "scroll",
    () => {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(move);
      }
    },
    { passive: true },
  );
}

setupStages();
setupVerify();
setupReveal();
setupBar();
setupHeroDrift();
document.documentElement.classList.add("is-ready");
refreshFromGestion().catch(() => {
  // Sin red o sin API: la copia prerenderizada sigue siendo la página.
});
