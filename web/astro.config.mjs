// ArtesaNFC public frontend — Astro configuration.
//
// Static output only: Cloudflare Pages serves the built `dist/` exactly like
// it serves `frontend/` today. There is no server runtime (no adapter, no
// Pages Functions): the public API stays the only source of truth and is
// called from the browser, as in frontend/ (docs/ARCHITECTURE.md §5, F-08).
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { defineConfig } from "astro/config";
import react from "@astrojs/react";

// <model-viewer> renders one <style> block inside its shadow root. The CSP
// allows exactly that block by hash, read from the installed package so an
// upgrade updates it; tests/e2e/csp.spec.ts fails if it ever stops matching.
function modelViewerStyleHashes() {
  const template = readFileSync(
    new URL("./node_modules/@google/model-viewer/lib/template.js", import.meta.url),
    "utf8",
  );
  return [...template.matchAll(/<style>([\s\S]*?)<\/style>/g)].map(
    ([, css]) => `sha256-${createHash("sha256").update(css).digest("base64")}`,
  );
}

export default defineConfig({
  site: "https://artesanfc.com",
  output: "static",
  trailingSlash: "ignore",
  build: {
    format: "directory",
    // Keep hashed bundles under one prefix so _headers can give them an
    // immutable cache policy.
    assets: "_astro",
  },
  integrations: [react()],
  // Content-Security-Policy as a <meta> in every page, with SHA-256 hashes of
  // the inline scripts and styles Astro emits (PEND-021). No third-party
  // origin: the API and its /media/ are the only other host, the 3D decoders
  // are self-hosted (scripts/copy-decoders.mjs). 'wasm-unsafe-eval' lets the
  // Draco/KTX2 WebAssembly decoders compile (it does not allow eval()).
  // http://127.0.0.1:8000 is the local API (api-config.ts refuses loopback on
  // any public host). frame-ancestors cannot go in a <meta>: X-Frame-Options
  // DENY in public/_headers covers framing.
  security: {
    csp: {
      algorithm: "SHA-256",
      scriptDirective: { resources: ["'self'", "'wasm-unsafe-eval'"] },
      styleDirective: { resources: ["'self'"], hashes: modelViewerStyleHashes() },
      directives: [
        "default-src 'self'",
        "connect-src 'self' https://api.artesanfc.com http://127.0.0.1:8000 blob: data:",
        "img-src 'self' https://api.artesanfc.com http://127.0.0.1:8000 blob: data:",
        "media-src 'self' https://api.artesanfc.com http://127.0.0.1:8000 blob:",
        "worker-src 'self' blob:",
        // Vite inlines small fonts as data: URIs.
        "font-src 'self' data:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-src 'none'",
      ],
    },
  },
  // No Astro prefetch, view transitions or image service: nothing is
  // fetched ahead of an explicit navigation, and media come from the API.
  devToolbar: { enabled: false },
  vite: {
    // The only chunk above Vite's 500 KB default is model-viewer + three.js,
    // loaded on demand after "Ver en 3D" (budgets: scripts/measure-assets.mjs).
    build: { chunkSizeWarningLimit: 1100 },
  },
});
