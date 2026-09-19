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

  // AuthenticityPublic shape (API_CONTRACT.md §7) — "authentic" carries
  // certificate_version/issued_at, "unavailable" carries nothing else.
  function isResolvePayload(payload) {
    if (!isPlainObject(payload) || !isPlainObject(payload.authenticity)) {
      return false;
    }
    var status = payload.authenticity.status;
    if (status === "unavailable") {
      return true;
    }
    if (status === "authentic") {
      return (
        typeof payload.authenticity.certificate_version === "number" &&
        typeof payload.authenticity.issued_at === "string" &&
        isPlainObject(payload.piece) &&
        isPlainObject(payload.artisan) &&
        isPlainObject(payload.authenticity_metadata)
      );
    }
    return false;
  }

  // POST /certificates/resolve (API_CONTRACT.md §7). Deliberately does NOT
  // reuse fetchJSON: that helper collapses every failure mode (network
  // error, timeout, non-2xx, malformed JSON) into the same `null` result,
  // which here would wrongly merge a transport/server failure into the
  // backend's canonical `{ authenticity: { status: "unavailable" } }`
  // convergence response (SECURITY.md §4 / API_CONTRACT.md §7). Callers
  // must be able to tell those apart, so this resolves to a tagged result
  // instead: { ok: true, status, payload } for a clean 200, or { ok: false }
  // for anything else. Never logs the token or any request detail.
  function resolveCertificate(token) {
    var controller = "AbortController" in global ? new AbortController() : null;
    var timeoutId = controller
      ? global.setTimeout(function () {
          controller.abort();
        }, TIMEOUT_MS)
      : null;

    function clearTimer() {
      if (timeoutId !== null) {
        global.clearTimeout(timeoutId);
      }
    }

    return global
      .fetch(getApiBase() + "/certificates/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ token: token }),
        signal: controller ? controller.signal : undefined
      })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("resolve request failed");
        }
        return response.json();
      })
      .then(function (data) {
        if (!isResolvePayload(data)) {
          throw new Error("resolve response malformed");
        }
        clearTimer();
        return { ok: true, status: data.authenticity.status, payload: data };
      })
      .catch(function () {
        clearTimer();
        return { ok: false };
      });
  }

  global.ArtesaNFC = global.ArtesaNFC || {};
  global.ArtesaNFC.api = {
    getArtisans: getArtisans,
    getArtisan: getArtisan,
    getPieces: getPieces,
    getPiece: getPiece,
    resolveCertificate: resolveCertificate
  };
})(window);
