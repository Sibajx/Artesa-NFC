// ArtesaNFC — API base configuration. Single source of truth for the API
// base URL: no HTML page declares it and no other script hardcodes it.
//
// The base is chosen from an exact-hostname allowlist. Any host not listed
// here (www, *.pages.dev previews, file://, lookalike domains, ...)
// resolves to null — "unresolved" — and the API layer then makes no network
// request at all, so public pages keep their static content and /c/{token}
// shows its service-error state. Supporting a new host (www, a staging
// preview) means adding it here explicitly, together with the matching
// CORS_ALLOWED_ORIGINS entry on the backend.

(function (global) {
  "use strict";

  var LOCAL_API_BASE = "http://127.0.0.1:8000/api/v1";
  var PRODUCTION_API_BASE = "https://api.artesanfc.com/api/v1";

  var LOCAL_HOSTS = ["localhost", "127.0.0.1"];

  var API_BASE_BY_HOST = {
    "localhost": LOCAL_API_BASE,
    "127.0.0.1": LOCAL_API_BASE,
    "artesanfc.com": PRODUCTION_API_BASE
  };

  var LOOPBACK_HOSTS = ["localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"];

  function isLoopbackUrl(url) {
    try {
      return LOOPBACK_HOSTS.indexOf(new URL(url).hostname.toLowerCase()) !== -1;
    } catch (error) {
      return true; // Unparseable — treat as unsafe.
    }
  }

  function resolveApiBase(hostname) {
    var host = String(hostname || "").toLowerCase();
    if (!Object.prototype.hasOwnProperty.call(API_BASE_BY_HOST, host)) {
      return null;
    }
    var base = API_BASE_BY_HOST[host];
    // Production guard: a non-local page must never end up talking to the
    // visitor's own machine, even if the table above is edited wrongly.
    if (LOCAL_HOSTS.indexOf(host) === -1 && isLoopbackUrl(base)) {
      return null;
    }
    return base;
  }

  var apiBase = resolveApiBase(global.location && global.location.hostname);
  var apiOrigin = apiBase ? new URL(apiBase).origin : null;

  // Media URLs are returned as root-relative paths (e.g. "/media/...")
  // against the API's origin, not the frontend's — API_CONTRACT.md §6.1.
  // With an unresolved base there is no origin to resolve against.
  function resolveMediaUrl(path) {
    if (!apiOrigin || !path) {
      return null;
    }
    if (/^https?:\/\//i.test(path)) {
      return path;
    }
    return apiOrigin + (path.charAt(0) === "/" ? path : "/" + path);
  }

  global.ArtesaNFC = global.ArtesaNFC || {};
  global.ArtesaNFC.apiConfig = {
    apiBase: apiBase,
    apiOrigin: apiOrigin,
    resolveMediaUrl: resolveMediaUrl
  };
})(window);
