// Lighthouse (mobile + desktop) over the built site served by serve-dist.mjs.
// Usage: node scripts/lighthouse.mjs [--base http://localhost:4330] [paths...]
// Needs a Chrome/Chromium: CHROME_PATH, or Playwright's bundled Chromium.
// Writes JSON reports to reports/lighthouse/ and prints a summary table.
import { mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { parseArgs } from "node:util";
import * as chromeLauncher from "chrome-launcher";
import lighthouse from "lighthouse";
import desktopConfig from "lighthouse/core/config/desktop-config.js";

const { values, positionals } = parseArgs({
  options: { base: { type: "string", default: "http://localhost:4330" } },
  allowPositionals: true,
});
const paths = positionals.length ? positionals : ["/", "/piezas/", "/artesanos/"];

function playwrightChromium() {
  const root = join(homedir(), ".cache", "ms-playwright");
  const dir = readdirSync(root).find((d) => /^chromium-\d+$/.test(d));
  return dir ? join(root, dir, "chrome-linux64", "chrome") : undefined;
}

const chromePath = process.env.CHROME_PATH ?? playwrightChromium();
const chrome = await chromeLauncher.launch({
  chromePath,
  chromeFlags: ["--headless=new", "--no-sandbox", "--disable-gpu"],
});
const out = new URL("../reports/lighthouse/", import.meta.url).pathname;
mkdirSync(out, { recursive: true });

const rows = [];
try {
  for (const formFactor of ["mobile", "desktop"]) {
    for (const path of paths) {
      const result = await lighthouse(
        values.base + path,
        { port: chrome.port, output: "json", logLevel: "error" },
        formFactor === "desktop" ? desktopConfig : undefined,
      );
      const lhr = result.lhr;
      const name = `${formFactor}${path.replace(/\/+/g, "_") || "_"}`;
      writeFileSync(join(out, `${name}.json`), result.report);
      const a = lhr.audits;
      rows.push({
        formFactor,
        path,
        perf: Math.round(lhr.categories.performance.score * 100),
        a11y: Math.round(lhr.categories.accessibility.score * 100),
        bp: Math.round(lhr.categories["best-practices"].score * 100),
        seo: Math.round(lhr.categories.seo.score * 100),
        lcp: a["largest-contentful-paint"].displayValue,
        cls: a["cumulative-layout-shift"].displayValue,
        tbt: a["total-blocking-time"].displayValue,
        bytes: a["total-byte-weight"].displayValue,
      });
    }
  }
} finally {
  await chrome.kill();
}
console.table(rows);
writeFileSync(join(out, "summary.json"), JSON.stringify(rows, null, 2));
