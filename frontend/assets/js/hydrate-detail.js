// ArtesaNFC — progressive enhancement for artisan/piece detail pages.
// Reads the slug from body[data-artisan-slug]/body[data-piece-slug],
// hydrates only the fields the API actually returns, and leaves every
// other section (including "Piezas relacionadas", which has no API
// backing) exactly as authored. Headings, ids and aria-labelledby
// relationships are never touched.

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

  function joinNonEmpty(parts, separator) {
    return parts
      .filter(function (part) {
        return part !== null && part !== undefined && String(part).trim() !== "";
      })
      .join(separator);
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
    img.src = url ? config.resolveMediaUrl(url) : PLACEHOLDER_JPG;
    if (url) {
      img.addEventListener("error", function onError() {
        img.removeEventListener("error", onError);
        img.src = PLACEHOLDER_JPG;
      });
    }

    picture.appendChild(img);
    return picture;
  }

  function setHeroImage(container, media) {
    if (!container || !media || !media.url) {
      return;
    }
    var img = container.querySelector("img");
    if (!img) {
      return;
    }
    // The static <picture> markup ships a <source type="image/webp"> sibling,
    // which always wins over img.src in browsers that support webp — remove
    // it first so the API-provided URL (and its error fallback) actually
    // takes effect instead of being silently ignored.
    container.querySelectorAll("source").forEach(function (source) {
      source.remove();
    });
    img.src = config.resolveMediaUrl(media.url);
    if (media.alt_text) {
      img.alt = media.alt_text;
    }
    img.addEventListener("error", function onError() {
      img.removeEventListener("error", onError);
      img.src = PLACEHOLDER_JPG;
    });
  }

  function replaceGallery(grid, mediaList) {
    if (!grid || !mediaList || !mediaList.length) {
      return;
    }
    var frag = document.createDocumentFragment();
    mediaList.forEach(function (media) {
      if (!media || media.type !== "image") {
        return;
      }
      var li = document.createElement("li");
      li.appendChild(buildImage(media.url, media.alt_text || ""));
      frag.appendChild(li);
    });
    if (frag.childNodes.length) {
      grid.textContent = "";
      grid.appendChild(frag);
    }
  }

  function replaceLeadParagraph(headingId, text) {
    if (!text) {
      return;
    }
    var heading = document.getElementById(headingId);
    var p = heading && heading.nextElementSibling;
    if (p && p.tagName === "P") {
      p.textContent = text;
    }
  }

  function hydrateArtisan(slug) {
    api.getArtisan(slug).then(function (artisan) {
      if (!artisan) {
        announce("Mostrando contenido de referencia: no se pudo cargar este perfil desde la API.");
        return;
      }

      var displayName = artisan.artistic_name || artisan.full_name;
      var titleEl = document.getElementById("artisan-title");
      var section = titleEl && titleEl.closest("section");

      if (titleEl && displayName) {
        titleEl.textContent = displayName;
      }

      var metaEl = titleEl && titleEl.nextElementSibling;
      if (metaEl && metaEl.classList.contains("card__meta")) {
        var locationText = joinNonEmpty(
          [artisan.location && artisan.location.locality, artisan.location && artisan.location.state],
          ", "
        );
        var techniqueText = artisan.techniques && artisan.techniques.length ? artisan.techniques[0] : "";
        var metaText = joinNonEmpty([locationText, techniqueText], " — ");
        if (metaText) {
          metaEl.textContent = metaText;
        }
      }

      var media = artisan.media || [];
      var portraitMedia = media.filter(function (m) {
        return m.role === "portrait";
      })[0];
      if (section) {
        setHeroImage(section.querySelector(".card__media"), portraitMedia);
      }

      replaceLeadParagraph("historia-title", artisan.history);

      var galeriaHeading = document.getElementById("galeria-title");
      var galeriaGrid = galeriaHeading && galeriaHeading.parentElement.querySelector(".card-grid");
      var galleryMedia = media.filter(function (m) {
        return m !== portraitMedia;
      });
      replaceGallery(galeriaGrid, galleryMedia);

      var piezasHeading = document.getElementById("piezas-title");
      var piezasGrid = piezasHeading && piezasHeading.parentElement.querySelector(".card-grid");
      if (piezasGrid && artisan.pieces && artisan.pieces.length) {
        var frag = document.createDocumentFragment();
        artisan.pieces.forEach(function (piece) {
          var coverUrl = piece.cover_media && piece.cover_media.url;
          var altText = (piece.cover_media && piece.cover_media.alt_text) || piece.name;

          var li = document.createElement("li");
          li.className = "card";
          li.appendChild(buildImage(coverUrl, altText));

          var title = document.createElement("p");
          title.className = "card__title";
          title.textContent = piece.name;
          li.appendChild(title);

          var action = document.createElement("a");
          action.className = "editorial-link card__action";
          action.href = "/piezas/" + encodeURIComponent(piece.slug) + "/";
          action.textContent = "Explorar pieza →";
          li.appendChild(action);

          frag.appendChild(li);
        });
        piezasGrid.textContent = "";
        piezasGrid.appendChild(frag);
      }
    });
  }

  function hydratePiece(slug) {
    api.getPiece(slug).then(function (piece) {
      if (!piece) {
        announce("Mostrando contenido de referencia: no se pudo cargar esta pieza desde la API.");
        return;
      }

      var titleEl = document.getElementById("piece-title");
      var section = titleEl && titleEl.closest("section");

      if (titleEl && piece.name) {
        titleEl.textContent = piece.name;
      }

      var metaEl = titleEl && titleEl.nextElementSibling;
      if (metaEl && metaEl.classList.contains("card__meta")) {
        var metaText = joinNonEmpty([piece.origin, piece.technique], " — ");
        if (metaText) {
          metaEl.textContent = metaText;
        }
      }

      var media = piece.media || [];
      var heroMedia =
        media.filter(function (m) {
          return m.role === "hero";
        })[0] || media[0];
      if (section) {
        setHeroImage(section.querySelector(".card__media"), heroMedia);
      }

      var galeriaHeading = document.getElementById("galeria-title");
      var galeriaGrid = galeriaHeading && galeriaHeading.parentElement.querySelector(".card-grid");
      var galleryMedia = media.filter(function (m) {
        return m !== heroMedia;
      });
      replaceGallery(galeriaGrid, galleryMedia);

      replaceLeadParagraph("historia-title", piece.history);

      var materialesHeading = document.getElementById("materiales-title");
      if (materialesHeading && piece.materials && piece.materials.length) {
        var list = materialesHeading.parentElement.querySelector('ul[role="list"]');
        if (list) {
          list.textContent = "";
          piece.materials.forEach(function (material) {
            var li = document.createElement("li");
            li.className = "card__descriptor";
            li.textContent = material;
            list.appendChild(li);
          });
        }
      }

      var artesanoHeading = document.getElementById("artesano-title");
      if (artesanoHeading && piece.artisan) {
        var artesanoSection = artesanoHeading.parentElement;
        var lead = artesanoSection.querySelector(".section-lead");
        var link = artesanoSection.querySelector(".editorial-link");
        var artisanName = piece.artisan.artistic_name || piece.artisan.full_name;

        if (lead) {
          lead.textContent = "Creada por " + artisanName + ".";
        }
        if (link) {
          link.textContent = "Creada por " + artisanName + " →";
          link.href = "/artesanos/" + encodeURIComponent(piece.artisan.slug) + "/";
        }
      }

      // "Piezas relacionadas" is intentionally left untouched: no
      // related-pieces API exists (approved decision, sprint 4 boundary).
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var artisanSlug = document.body.getAttribute("data-artisan-slug");
    var pieceSlug = document.body.getAttribute("data-piece-slug");

    if (artisanSlug) {
      hydrateArtisan(artisanSlug);
    } else if (pieceSlug) {
      hydratePiece(pieceSlug);
    }
  });
})();
