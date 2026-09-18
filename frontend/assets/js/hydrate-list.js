// ArtesaNFC — progressive enhancement for /artesanos and /piezas.
// Static cards already in the page are the no-JS / API-down fallback.
// They are only replaced after a successful, non-empty API response;
// any failure or empty result leaves them exactly as rendered.

(function () {
  "use strict";

  var api = window.ArtesaNFC && window.ArtesaNFC.api;
  var config = window.ArtesaNFC && window.ArtesaNFC.apiConfig;
  if (!api || !config) {
    return;
  }

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

    if (url) {
      img.src = config.resolveMediaUrl(url);
      img.addEventListener("error", function onError() {
        img.removeEventListener("error", onError);
        img.src = PLACEHOLDER_JPG;
      });
    } else {
      // ArtisanSummary carries no media (API_CONTRACT.md §8) — placeholder
      // is the correct steady state here, not a fallback from a failure.
      img.src = PLACEHOLDER_JPG;
    }

    picture.appendChild(img);
    return picture;
  }

  function hydrateArtisans(list) {
    var section = document.getElementById("artesanos");
    var grid = section && section.querySelector(".card-grid");
    if (!grid) {
      return;
    }

    var items = list.filter(function (a) {
      return a && typeof a.slug === "string" && typeof a.full_name === "string";
    });
    if (!items.length) {
      announce("Mostrando contenido de referencia: no se pudo cargar el directorio de artesanos desde la API.");
      return;
    }

    var frag = document.createDocumentFragment();
    items.forEach(function (artisan) {
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

      frag.appendChild(li);
    });

    grid.textContent = "";
    grid.appendChild(frag);
  }

  function hydratePieces(list) {
    var section = document.getElementById("piezas");
    var grid = section && section.querySelector(".card-grid");
    if (!grid) {
      return;
    }

    var items = list.filter(function (p) {
      return p && typeof p.slug === "string" && typeof p.name === "string";
    });
    if (!items.length) {
      announce("Mostrando contenido de referencia: no se pudo cargar el catálogo de piezas desde la API.");
      return;
    }

    var frag = document.createDocumentFragment();
    items.forEach(function (piece) {
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

      frag.appendChild(li);
    });

    grid.textContent = "";
    grid.appendChild(frag);
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (document.getElementById("artesanos")) {
      api.getArtisans().then(function (payload) {
        if (payload && Array.isArray(payload.data) && payload.data.length) {
          hydrateArtisans(payload.data);
        } else {
          announce("Mostrando contenido de referencia: no se pudo cargar el directorio de artesanos desde la API.");
        }
      });
    }

    if (document.getElementById("piezas")) {
      api.getPieces().then(function (payload) {
        if (payload && Array.isArray(payload.data) && payload.data.length) {
          hydratePieces(payload.data);
        } else {
          announce("Mostrando contenido de referencia: no se pudo cargar el catálogo de piezas desde la API.");
        }
      });
    }
  });
})();
