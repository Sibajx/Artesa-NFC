// ArtesaNFC — /artesanos and /piezas listings (F-08).
//
// The two list pages ship NO entity cards: what is listed comes only from the
// public API, which is the sole authority on publication. A valid 200 with
// items renders them; a valid 200 with `data: []` is the authoritative "nothing
// is published" and shows an empty state (it is NOT a failure, so nothing old
// is kept); every other outcome (5xx, 429, timeout, network error, malformed
// response, a host with no API base) shows a neutral unavailable state with a
// retry control. Cards are built with textContent / encodeURIComponent only.

(function () {
  "use strict";

  var api = window.ArtesaNFC && window.ArtesaNFC.api;
  var config = window.ArtesaNFC && window.ArtesaNFC.apiConfig;

  var PLACEHOLDER_JPG = "/assets/img/card-placeholder.jpg";

  function announce(message) {
    var status = document.getElementById("hydrate-status");
    if (status) {
      status.textContent = message;
    }
  }

  function buildImage(url, alt) {
    var picture = document.createElement("picture");
    picture.className = "card__media";

    var img = document.createElement("img");
    img.className = "card__media-img";
    img.loading = "lazy";
    img.decoding = "async";
    img.width = 640;
    img.height = 800;
    img.alt = alt || "";

    var resolved = url ? config.resolveMediaUrl(url) : null;
    if (resolved) {
      img.src = resolved;
      img.addEventListener("error", function onError() {
        img.removeEventListener("error", onError);
        img.src = PLACEHOLDER_JPG;
      });
    } else {
      // ArtisanSummary carries no media (API_CONTRACT.md §8) — the placeholder
      // is the correct steady state here, not a fallback from a failure.
      img.src = PLACEHOLDER_JPG;
    }

    picture.appendChild(img);
    return picture;
  }

  function buildArtisanCard(artisan) {
    var displayName = artisan.artistic_name || artisan.full_name;

    var li = document.createElement("li");
    li.className = "card";
    li.appendChild(buildImage(null, "Retrato de " + displayName));

    var title = document.createElement("p");
    title.className = "card__title";
    title.textContent = displayName;
    li.appendChild(title);

    var action = document.createElement("a");
    action.className = "editorial-link card__action";
    action.href = "/artesanos/" + encodeURIComponent(artisan.slug) + "/";
    action.textContent = "Conocer al artesano →";
    li.appendChild(action);
    return li;
  }

  function buildPieceCard(piece) {
    var mediaUrl = piece.cover_media && piece.cover_media.url;
    var altText = (piece.cover_media && piece.cover_media.alt_text) || piece.name;

    var li = document.createElement("li");
    li.className = "card";
    li.appendChild(buildImage(mediaUrl, altText));

    var title = document.createElement("p");
    title.className = "card__title";
    title.textContent = piece.name;
    li.appendChild(title);

    // ArtisanPieceSummary has no artisan name field — never fabricate one
    // (API_CONTRACT.md §8); public_code is the one extra field it does
    // provide, so it's the only meta line shown here.
    if (piece.public_code) {
      var meta = document.createElement("p");
      meta.className = "card__meta";
      meta.textContent = "Código público: " + piece.public_code;
      li.appendChild(meta);
    }

    var action = document.createElement("a");
    action.className = "editorial-link card__action";
    action.href = "/piezas/" + encodeURIComponent(piece.slug) + "/";
    action.textContent = "Explorar pieza →";
    li.appendChild(action);
    return li;
  }

  var LISTS = {
    artesanos: {
      sectionId: "artesanos",
      fetch: function () {
        return api.getArtisans();
      },
      isValid: function (a) {
        return !!a && typeof a.slug === "string" && typeof a.full_name === "string";
      },
      build: buildArtisanCard,
      emptyMessage: "Aún no hay artesanos publicados.",
      unavailableMessage: "No se pudo cargar el directorio de artesanos."
    },
    piezas: {
      sectionId: "piezas",
      fetch: function () {
        return api.getPieces();
      },
      isValid: function (p) {
        return !!p && typeof p.slug === "string" && typeof p.name === "string";
      },
      build: buildPieceCard,
      emptyMessage: "Aún no hay piezas publicadas.",
      unavailableMessage: "No se pudo cargar el catálogo de piezas."
    }
  };

  function setState(section, name) {
    var parts = {
      loading: section.querySelector("#list-loading"),
      empty: section.querySelector("#list-empty"),
      unavailable: section.querySelector("#list-unavailable"),
      list: section.querySelector(".card-grid")
    };
    Object.keys(parts).forEach(function (key) {
      if (parts[key]) {
        parts[key].hidden = key !== name;
      }
    });
    section.setAttribute("aria-busy", name === "loading" ? "true" : "false");
  }

  var requestIds = {};

  function load(list) {
    var section = document.getElementById(list.sectionId);
    var grid = section && section.querySelector(".card-grid");
    if (!section || !grid) {
      return;
    }

    var mine = (requestIds[list.sectionId] = (requestIds[list.sectionId] || 0) + 1);
    grid.textContent = "";
    setState(section, "loading");
    announce("Cargando…");

    var request = api && config ? list.fetch() : Promise.resolve({ status: "unavailable" });
    request.then(function (result) {
      if (mine !== requestIds[list.sectionId]) {
        return;
      }

      if (!result || result.status !== "ok" || !result.data || !Array.isArray(result.data.data)) {
        setState(section, "unavailable");
        announce(list.unavailableMessage);
        return;
      }

      var items = result.data.data;
      if (items.length === 0) {
        setState(section, "empty");
        announce(list.emptyMessage);
        return;
      }

      var valid = items.filter(list.isValid);
      if (valid.length === 0) {
        // Non-empty but nothing usable: the payload is malformed, which is a
        // failure to find out, not an authoritative "empty".
        setState(section, "unavailable");
        announce(list.unavailableMessage);
        return;
      }

      var frag = document.createDocumentFragment();
      valid.forEach(function (item) {
        frag.appendChild(list.build(item));
      });
      grid.appendChild(frag);
      setState(section, "list");
      announce("");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    Object.keys(LISTS).forEach(function (key) {
      var list = LISTS[key];
      var section = document.getElementById(list.sectionId);
      if (!section) {
        return;
      }
      var retry = section.querySelector("#list-retry");
      if (retry) {
        retry.addEventListener("click", function () {
          load(list);
        });
      }
      load(list);
    });
  });
})();
