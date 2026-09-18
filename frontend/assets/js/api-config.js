// ArtesaNFC — API base configuration.
// Reads the <meta name="artesanfc-api-base"> tag when present and falls
// back to "/api/v1" otherwise (API_CONTRACT.md, frontend integration
// issue). No production deployment logic beyond this configurable
// mechanism.

(function (global) {
  "use strict";

  var DEFAULT_API_BASE = "/api/v1";

  var metaTag = document.querySelector('meta[name="artesanfc-api-base"]');
  var rawBase =
    (metaTag && metaTag.getAttribute("content") && metaTag.getAttribute("content").trim()) ||
    DEFAULT_API_BASE;
  var apiBase = rawBase.replace(/\/+$/, "");

  var apiOrigin;
  try {
    apiOrigin = new URL(apiBase, global.location.origin).origin;
  } catch (error) {
    apiOrigin = global.location.origin;
  }

  // Media URLs are returned as root-relative paths (e.g. "/media/...")
  // against the API's origin, not the frontend's — API_CONTRACT.md §6.1.
  function resolveMediaUrl(path) {
    if (!path) {
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
