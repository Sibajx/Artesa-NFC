// ArtesaNFC public frontend — Astro configuration.
//
// Static output only: Cloudflare Pages serves the built `dist/` exactly like
// it serves `frontend/` today. There is no server runtime (no adapter, no
// Pages Functions): the public API stays the only source of truth and is
// called from the browser, as in frontend/ (docs/ARCHITECTURE.md §5, F-08).
import { defineConfig } from "astro/config";
import react from "@astrojs/react";

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
  // No Astro prefetch, view transitions or image service: nothing is
  // fetched ahead of an explicit navigation, and media come from the API.
  devToolbar: { enabled: false },
  vite: {
    // The only chunk above Vite's 500 KB default is model-viewer + three.js,
    // loaded on demand after "Ver en 3D" (budgets: scripts/measure-assets.mjs).
    build: { chunkSizeWarningLimit: 1100 },
  },
});
