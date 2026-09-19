// ArtesaNFC — private certificate route (/c/{token}), Issue #72.
//
// Resolves the bearer token against POST /certificates/resolve and renders
// exactly one of four states: loading, authentic, unavailable, or a
// separate temporary service-error state for transport failures (these
// must never collapse into "unavailable" — SECURITY.md §4).
//
// Privacy invariants (SECURITY.md, this issue's spec):
// - the token is read only from location.pathname, never a query string
// - the token is never written into DOM text/attributes, storage, or
//   logged to the console — it lives only in local variables for the
//   duration of this request
// - no route-structure fallback pattern gets more lenient than the
//   canonical /c/{token} shape (exactly one non-empty path segment)

(function (global) {
  "use strict";

  var STATE_IDS = ["cert-loading", "cert-unavailable", "cert-error", "cert-authentic"];
  var PLACEHOLDER_JPG = "/assets/img/card-placeholder.jpg";
  var TOKEN_SHAPE = /^[A-Za-z0-9_-]{1,256}$/;

  function showState(name) {
    STATE_IDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) {
        el.hidden = id !== name;
      }
    });
  }

  function joinNonEmpty(parts, separator) {
    return parts
      .filter(function (part) {
        return part !== null && part !== undefined && String(part).trim() !== "";
      })
      .join(separator);
  }

  // Canonical route shape only: /c/{token} — exactly one non-empty
  // segment, no trailing slash, no nested segments, no query-string
  // fallback (the caller never looks at location.search). This is a
  // route-structure sanity check, not authenticity validation: the
  // backend alone decides whether a token is real (SECURITY.md §4).
  function extractToken(pathname) {
    var PREFIX = "/c/";
    if (typeof pathname !== "string" || pathname.indexOf(PREFIX) !== 0) {
      return null;
    }
    var rest = pathname.slice(PREFIX.length);
    if (rest === "" || rest.indexOf("/") !== -1) {
      return null;
    }
    var decoded;
    try {
      decoded = decodeURIComponent(rest);
    } catch (error) {
      // Malformed percent-encoding — fail safe, never log the raw path.
      return null;
    }
    if (!TOKEN_SHAPE.test(decoded)) {
      return null;
    }
    return decoded;
  }

  function setImage(imgEl, media, fallbackAlt, config) {
    if (!imgEl) {
      return;
    }
    if (media && media.url) {
      imgEl.src = config.resolveMediaUrl(media.url);
      imgEl.alt = media.alt_text || fallbackAlt || "";
      imgEl.addEventListener("error", function onError() {
        imgEl.removeEventListener("error", onError);
        imgEl.src = PLACEHOLDER_JPG;
      });
    } else {
      imgEl.src = PLACEHOLDER_JPG;
      imgEl.alt = fallbackAlt || "";
    }
  }

  function formatIssuedAt(isoDatetime) {
    try {
      var parsed = new Date(isoDatetime);
      if (isNaN(parsed.getTime())) {
        return "";
      }
      return parsed.toLocaleDateString("es-MX", { year: "numeric", month: "long", day: "numeric" });
    } catch (error) {
      return "";
    }
  }

  // Renders only fields present in the approved resolve response
  // (API_CONTRACT.md §7). No internal UUIDs, token, token_hash, or NFC
  // fields exist on this payload to begin with, so there is nothing to
  // filter out beyond rendering exactly these fields and no others.
  function renderAuthentic(payload, config) {
    var piece = payload.piece;
    var artisan = payload.artisan;
    var authenticity = payload.authenticity;
    var metadata = payload.authenticity_metadata;

    var titleEl = document.getElementById("cert-piece-title");
    if (titleEl) {
      titleEl.textContent = piece.name || "";
    }

    var metaEl = document.getElementById("cert-piece-meta");
    if (metaEl) {
      metaEl.textContent = joinNonEmpty([piece.origin, piece.technique], " — ");
    }

    var descriptionEl = document.getElementById("cert-piece-description");
    if (descriptionEl) {
      descriptionEl.textContent = piece.description || piece.history || "";
    }

    var media = piece.media || [];
    var heroMedia =
      media.filter(function (m) {
        return m && m.role === "hero";
      })[0] || media[0];
    setImage(document.getElementById("cert-piece-image"), heroMedia, piece.name, config);

    var versionEl = document.getElementById("cert-version");
    if (versionEl) {
      versionEl.textContent = String(authenticity.certificate_version);
    }

    var issuedEl = document.getElementById("cert-issued-at");
    if (issuedEl) {
      issuedEl.textContent = formatIssuedAt(authenticity.issued_at) || authenticity.issued_at;
    }

    var notesEl = document.getElementById("cert-notes");
    if (notesEl) {
      if (metadata && metadata.notes) {
        notesEl.textContent = metadata.notes;
        notesEl.hidden = false;
      } else {
        notesEl.hidden = true;
      }
    }

    var artisanNameEl = document.getElementById("cert-artisan-name");
    if (artisanNameEl) {
      artisanNameEl.textContent = artisan.artistic_name || artisan.full_name || "";
    }

    var artisanLocationEl = document.getElementById("cert-artisan-location");
    if (artisanLocationEl) {
      artisanLocationEl.textContent = joinNonEmpty(
        [artisan.location && artisan.location.locality, artisan.location && artisan.location.state],
        ", "
      );
    }

    // Public slugs only — never carries the certificate token, and the
    // page's no-referrer policy keeps it out of the Referer header too.
    var artisanLinkEl = document.getElementById("cert-artisan-link");
    if (artisanLinkEl && artisan.slug) {
      artisanLinkEl.href = "/artesanos/" + encodeURIComponent(artisan.slug) + "/";
    }

    var pieceLinkEl = document.getElementById("cert-piece-link");
    if (pieceLinkEl && piece.slug) {
      pieceLinkEl.href = "/piezas/" + encodeURIComponent(piece.slug) + "/";
    }

    showState("cert-authentic");
  }

  document.addEventListener("DOMContentLoaded", function () {
    var api = global.ArtesaNFC && global.ArtesaNFC.api;
    var config = global.ArtesaNFC && global.ArtesaNFC.apiConfig;

    var token = extractToken(global.location.pathname);
    if (!token) {
      // Missing or malformed route shape — no API call, safe neutral state.
      showState("cert-unavailable");
      return;
    }

    if (!api || !config || typeof api.resolveCertificate !== "function") {
      showState("cert-error");
      return;
    }

    showState("cert-loading");

    api.resolveCertificate(token).then(function (result) {
      // `token` and `result` go out of scope once this callback returns;
      // nothing here is assigned to window/localStorage/sessionStorage/
      // cookies, and nothing here is logged.
      if (!result || !result.ok) {
        showState("cert-error");
        return;
      }
      if (result.status === "authentic") {
        renderAuthentic(result.payload, config);
      } else {
        showState("cert-unavailable");
      }
    });
  });
})(window);
