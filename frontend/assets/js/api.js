// ArtesaNFC — public API fetch wrapper.
// Vanilla JS, no dependencies. Every request is time-boxed with
// AbortController (~5s) and every failure mode (network error, timeout,
// non-2xx, JSON parse error, malformed shape) resolves to null instead of
// throwing, so a caller can always fall back to the static page.

(function (global) {
  "use strict";

  var TIMEOUT_MS = 5000;

  function getApiBase() {
    return (global.ArtesaNFC && global.ArtesaNFC.apiConfig && global.ArtesaNFC.apiConfig.apiBase) || "/api/v1";
  }

  function isPlainObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  // Listing envelope shape (API_CONTRACT.md §8): { data: [...], meta: {...} }
  function isListEnvelope(payload) {
    return isPlainObject(payload) && Array.isArray(payload.data);
  }

  // ArtisanPublic shape (API_CONTRACT.md §4) — checked loosely, just enough
  // to reject a response that isn't shaped like this contract.
  function isArtisanPublic(payload) {
    return (
      isPlainObject(payload) &&
      typeof payload.slug === "string" &&
      typeof payload.full_name === "string" &&
      Array.isArray(payload.pieces) &&
      Array.isArray(payload.media)
    );
  }

  // PiecePublic shape (API_CONTRACT.md §5).
  function isPiecePublic(payload) {
    return (
      isPlainObject(payload) &&
      typeof payload.slug === "string" &&
      typeof payload.name === "string" &&
      isPlainObject(payload.artisan) &&
      Array.isArray(payload.media)
    );
  }

  function fetchJSON(path, validate) {
    var controller = "AbortController" in global ? new AbortController() : null;
    var timeoutId = controller
      ? global.setTimeout(function () {
          controller.abort();
        }, TIMEOUT_MS)
      : null;
    var url = getApiBase() + path;

    return global
      .fetch(url, {
        method: "GET",
        headers: { Accept: "application/json" },
        signal: controller ? controller.signal : undefined
      })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (data) {
        if (validate && !validate(data)) {
          throw new Error("Malformed response shape for " + path);
        }
        return data;
      })
      .catch(function (error) {
        console.warn(
          "[ArtesaNFC] API request failed (" + url + "): " + (error && error.message ? error.message : error)
        );
        return null;
      })
      .then(function (result) {
        if (timeoutId !== null) {
          global.clearTimeout(timeoutId);
        }
        return result;
      });
  }

  function getArtisans() {
    return fetchJSON("/artisans", isListEnvelope);
  }

  function getArtisan(slug) {
    return fetchJSON("/artisans/" + encodeURIComponent(slug), isArtisanPublic);
  }

  function getPieces() {
    return fetchJSON("/pieces", isListEnvelope);
  }

  function getPiece(slug) {
    return fetchJSON("/pieces/" + encodeURIComponent(slug), isPiecePublic);
  }

  global.ArtesaNFC = global.ArtesaNFC || {};
  global.ArtesaNFC.api = {
    getArtisans: getArtisans,
    getArtisan: getArtisan,
    getPieces: getPieces,
    getPiece: getPiece
  };
})(window);
