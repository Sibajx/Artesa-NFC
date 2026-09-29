// ArtesaNFC — API base configuration. Single source of truth for the API base
// URL in the web app: no page, component or script declares or duplicates it.
//
// Port of frontend/assets/js/api-config.js (docs/ARCHITECTURE.md §11). The
// base is chosen from an exact-hostname allowlist at runtime. Any host not
// listed (www, *.pages.dev previews, lookalike domains, ...) resolves to null
// ("unresolved") and the API layer makes no network request at all.
// Supporting a new host (www, a staging preview) means adding it here
// explicitly, together with the matching CORS_ALLOWED_ORIGINS entry on the
// backend. There is deliberately no build-time env override: a wrong value
// baked into a production build could point visitors at another API.

const LOCAL_API_BASE = "http://127.0.0.1:8000/api/v1";
const PRODUCTION_API_BASE = "https://api.artesanfc.com/api/v1";

const LOCAL_HOSTS: readonly string[] = ["localhost", "127.0.0.1"];
const LOOPBACK_HOSTS: readonly string[] = ["localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"];

const API_BASE_BY_HOST: Readonly<Record<string, string>> = {
  localhost: LOCAL_API_BASE,
  "127.0.0.1": LOCAL_API_BASE,
  "artesanfc.com": PRODUCTION_API_BASE,
  // Staging of the Astro app (web/README.md, "Staging paso a paso"): reads
  // the production API, so the backend must list https://staging.artesanfc.com
  // in CORS_ALLOWED_ORIGINS. Without that CORS entry it degrades to the
  // neutral "service unavailable" state, never to wrong data.
  "staging.artesanfc.com": PRODUCTION_API_BASE,
};

function isLoopbackUrl(url: string): boolean {
  try {
    return LOOPBACK_HOSTS.includes(new URL(url).hostname.toLowerCase());
  } catch {
    return true; // Unparseable: treat as unsafe.
  }
}

export function resolveApiBase(hostname: string | null | undefined): string | null {
  const host = String(hostname ?? "").toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(API_BASE_BY_HOST, host)) {
    return null;
  }
  const base = API_BASE_BY_HOST[host];
  if (base === undefined) {
    return null;
  }
  // Production guard: a non-local page must never talk to the visitor's own
  // machine, even if the table above is edited wrongly.
  if (!LOCAL_HOSTS.includes(host) && isLoopbackUrl(base)) {
    return null;
  }
  return base;
}

export interface ApiConfig {
  readonly apiBase: string | null;
  readonly apiOrigin: string | null;
}

export function apiConfigFor(hostname: string | null | undefined): ApiConfig {
  const apiBase = resolveApiBase(hostname);
  return { apiBase, apiOrigin: apiBase ? new URL(apiBase).origin : null };
}

export function currentApiConfig(): ApiConfig {
  return apiConfigFor(typeof window === "undefined" ? null : window.location.hostname);
}

// Media URLs are returned as root-relative paths ("/media/...") against the
// API origin, not the frontend's (API_CONTRACT.md §6.1). Absolute http(s)
// URLs (a future CDN) pass through; anything else is refused.
export function resolveMediaUrl(path: string | null | undefined, config: ApiConfig): string | null {
  if (!path) {
    return null;
  }
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  if (!config.apiOrigin || /^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith("//")) {
    return null;
  }
  return config.apiOrigin + (path.startsWith("/") ? path : `/${path}`);
}
