// Measures what each page downloads up front (HTML + referenced CSS/JS/
// fonts/images, raw and gzip) and the weight of every media asset, and fails
// if a budget is exceeded. Reads the built dist/ only (run after `build`).
//
// Budgets (initial, gzip): home JS 15 KB, data pages JS 90 KB, CSS 30 KB.
// Media: hero video ≤ 4 MB per file, stills ≤ 400 KB, GLB 2–8 MB target
// (models come from the API/CDN, not from dist; checked when present).
import { readFileSync, readdirSync, statSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { extname, join, relative } from "node:path";

const DIST = new URL("../dist/", import.meta.url).pathname;
const KB = 1024;
const budgets = {
  homeJs: 15 * KB,
  pageJs: 90 * KB,
  css: 30 * KB,
  video: 4 * 1024 * KB,
  still: 400 * KB,
  glbMax: 8 * 1024 * KB,
};

const walk = (dir) =>
  readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
const gz = (buf) => gzipSync(buf, { level: 9 }).length;
const fmt = (n) =>
  n >= 1024 * KB ? `${(n / 1024 / KB).toFixed(2)} MB` : `${(n / KB).toFixed(1)} KB`;

const failures = [];
const pages = walk(DIST).filter((f) => f.endsWith(".html"));

// Follows static imports of JS modules so the initial JS closure is counted.
function jsClosure(entry, seen = new Set()) {
  if (seen.has(entry)) return seen;
  seen.add(entry);
  const src = readFileSync(entry, "utf8");
  for (const [, spec] of src.matchAll(
    /(?:^|[;\s])import(?:[^"'()]*?from)?\s*["'](\.\/[^"']+)["']/g,
  )) {
    jsClosure(join(entry, "..", spec), seen);
  }
  return seen;
}

console.log("Initial page weight (static references; lazy chunks excluded)\n");
console.log(
  "page".padEnd(28),
  "html(gz)".padStart(10),
  "css(gz)".padStart(10),
  "js(gz)".padStart(10),
);
for (const page of pages) {
  const html = readFileSync(page, "utf8");
  const rel = "/" + relative(DIST, page);
  const css = [...html.matchAll(/href="(\/_astro\/[^"]+\.css)"/g)].map((m) => m[1]);
  const entries = [
    ...[...html.matchAll(/(?:src|component-url|renderer-url)="(\/_astro\/[^"]+\.js)"/g)].map(
      (m) => m[1],
    ),
  ];
  const js = new Set();
  for (const e of entries) for (const f of jsClosure(join(DIST, e))) js.add(f);
  const inlineJs = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)]
    .map((m) => m[1])
    .join("");
  const cssBytes = css.reduce((sum, f) => sum + gz(readFileSync(join(DIST, f))), 0);
  const jsBytes =
    [...js].reduce((sum, f) => sum + gz(readFileSync(f)), 0) + gz(Buffer.from(inlineJs));
  console.log(
    rel.padEnd(28),
    fmt(gz(Buffer.from(html))).padStart(10),
    fmt(cssBytes).padStart(10),
    fmt(jsBytes).padStart(10),
  );
  const jsBudget = rel === "/index.html" || rel === "/404.html" ? budgets.homeJs : budgets.pageJs;
  if (jsBytes > jsBudget) failures.push(`${rel}: JS ${fmt(jsBytes)} > ${fmt(jsBudget)}`);
  if (cssBytes > budgets.css) failures.push(`${rel}: CSS ${fmt(cssBytes)} > ${fmt(budgets.css)}`);
  if ([...js].some((f) => f.includes("model-viewer")))
    failures.push(`${rel}: model-viewer in initial JS`);
}

const lazy = walk(join(DIST, "_astro")).filter(
  (f) => f.includes("model-viewer") && f.endsWith(".js"),
);
for (const f of lazy) {
  const buf = readFileSync(f);
  console.log(
    `\nLazy 3D chunk (on demand only): ${relative(DIST, f)} ${fmt(buf.length)} (${fmt(gz(buf))} gz)`,
  );
}

console.log("\nMedia assets");
for (const file of walk(DIST).filter((f) => /\.(webm|mp4|avif|webp|jpe?g|png|glb)$/i.test(f))) {
  const size = statSync(file).size;
  const ext = extname(file).toLowerCase();
  console.log(`  ${relative(DIST, file).padEnd(48)} ${fmt(size).padStart(10)}`);
  if ((ext === ".webm" || ext === ".mp4") && size > budgets.video)
    failures.push(`${file}: video > 4 MB`);
  if ([".avif", ".webp", ".jpg", ".jpeg", ".png"].includes(ext) && size > budgets.still)
    failures.push(`${file}: image > 400 KB`);
  if (ext === ".glb" && size > budgets.glbMax) failures.push(`${file}: GLB > 8 MB`);
}

const fonts = walk(DIST).filter((f) => f.endsWith(".woff2"));
console.log(
  `\nFonts (woff2, only the latin subsets are fetched by the pages): ${fonts.length} files`,
);

if (failures.length) {
  console.error("\nBudget failures:\n  " + failures.join("\n  "));
  process.exit(1);
}
console.log("\nAll budgets met.");
