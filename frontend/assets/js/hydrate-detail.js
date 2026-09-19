// ArtesaNFC — public artisan/piece detail pages (F-08).
//
// The two detail routes are served by ONE neutral shell per entity type
// (/_shell/pieza/, /_shell/artesano/) that carries no entity content. This
// script is the only thing that ever puts entity content on the page, and it
// does so only after the public API answers 200 with a valid payload — the
// API is the sole authority on publication. Every other outcome shows a
// neutral state instead:
//
//   ok           the entity, rendered from the API payload only
//   not_found    HTTP 404 (unknown / draft / archived / unpublished owner —
//                the API deliberately does not tell these apart, so neither
//                does the page)
//   unavailable  5xx, 429, timeout, network error, malformed response, a host
//                with no API base, or a URL that is not exactly one slug
//
// The slug is read from location.pathname. Nothing here reads content from
// the HTML, and nothing is built with innerHTML: text goes through
// textContent, URLs through encodeURIComponent.

(function () {
  "use strict";

  var api = window.ArtesaNFC && window.ArtesaNFC.api;
  var config = window.ArtesaNFC && window.ArtesaNFC.apiConfig;

  var PLACEHOLDER_JPG = "/assets/img/card-placeholder.jpg";
  var STATES = ["loading", "not-found", "unavailable", "content"];
  var MAX_SLUG_LENGTH = 200;

  var requestId = 0;

  function byId(id) {
    return document.getElementById(id);
  }

  function announce(message) {
    var status = byId("hydrate-status");
    if (status) {
      status.textContent = message;
    }
  }

  function showState(name) {
    STATES.forEach(function (state) {
      var el = byId("entity-" + state);
      if (el) {
        el.hidden = state !== name;
      }
    });
    var main = byId("main-content");
    if (main) {
      main.setAttribute("aria-busy", name === "loading" ? "true" : "false");
    }
  }

  // Everything except a valid 200 must stay out of search results: the shell
  // answers 200 for any single segment, so a slug that does not exist would
  // otherwise look like an indexable page.
  function setIndexable(indexable) {
    var meta = document.querySelector('meta[name="robots"]');
    if (indexable) {
      if (meta) {
        meta.remove();
      }
      return;
    }
    if (!meta) {
      meta = document.createElement("meta");
      meta.setAttribute("name", "robots");
      document.head.appendChild(meta);
    }
    meta.setAttribute("content", "noindex");
  }

  function setMetadata(title, description, canonicalPath) {
    document.title = title + " — ArtesaNFC";

    var desc = document.querySelector('meta[name="description"]');
    if (desc && description) {
      desc.setAttribute("content", description);
    }

    var link = document.querySelector('link[rel="canonical"]');
    if (!link) {
      link = document.createElement("link");
      link.setAttribute("rel", "canonical");
      document.head.appendChild(link);
    }
    link.setAttribute("href", window.location.origin + canonicalPath);
  }

  function joinNonEmpty(parts, separator) {
    return parts
      .filter(function (part) {
        return part !== null && part !== undefined && String(part).trim() !== "";
      })
      .join(separator);
  }

  function setText(id, text) {
    var el = byId(id);
    if (!el) {
      return null;
    }
    el.textContent = text || "";
    el.hidden = !text;
    return el;
  }

  // Sections are hidden unless the payload has data for them: an empty
  // section must never be filled with anything the API did not send.
  function setSection(id, visible) {
    var el = byId(id);
    if (el) {
      el.hidden = !visible;
    }
    return el;
  }

  function images(mediaList) {
    return (mediaList || []).filter(function (media) {
      return media && media.type === "image" && media.url;
    });
  }

  function buildImage(url, alt, options) {
    var hero = !!(options && options.hero);
    var picture = document.createElement("picture");
    picture.className = hero ? "card__media artisan-portrait" : "card__media";

    var img = document.createElement("img");
    img.className = "card__media-img";
    img.loading = hero ? "eager" : "lazy";
    img.decoding = "async";
    img.width = hero ? 960 : 640;
    img.height = hero ? 540 : 800;
    img.alt = alt || "";

    var resolved = url ? config.resolveMediaUrl(url) : null;
    img.src = resolved || PLACEHOLDER_JPG;
    if (resolved) {
      img.addEventListener("error", function onError() {
        img.removeEventListener("error", onError);
        img.src = PLACEHOLDER_JPG;
      });
    }

    picture.appendChild(img);
    return picture;
  }

  function fillHero(containerId, media, fallbackAlt) {
    var container = byId(containerId);
    if (!container) {
      return;
    }
    container.textContent = "";
    if (media) {
      container.appendChild(buildImage(media.url, media.alt_text || fallbackAlt, { hero: true }));
    }
  }

  function fillGallery(sectionId, mediaList, fallbackAlt) {
    var section = byId(sectionId);
    var grid = section && section.querySelector(".card-grid");
    if (!grid) {
      return;
    }
    grid.textContent = "";
    mediaList.forEach(function (media) {
      var li = document.createElement("li");
      li.appendChild(buildImage(media.url, media.alt_text || fallbackAlt));
      grid.appendChild(li);
    });
    section.hidden = mediaList.length === 0;
  }

  function fillLead(sectionId, text) {
    var section = byId(sectionId);
    var p = section && section.querySelector("p");
    if (!p) {
      return;
    }
    p.textContent = text || "";
    section.hidden = !text;
  }

  function fillDescriptors(sectionId, items) {
    var section = byId(sectionId);
    var list = section && section.querySelector("ul");
    if (!list) {
      return;
    }
    list.textContent = "";
    var values = (items || []).filter(function (item) {
      return typeof item === "string" && item.trim() !== "";
    });
    values.forEach(function (value) {
      var li = document.createElement("li");
      li.className = "card__descriptor";
      li.textContent = value;
      list.appendChild(li);
    });
    section.hidden = values.length === 0;
  }

  function renderPiece(piece) {
    setText("piece-title", piece.name);
    setText("piece-meta", joinNonEmpty([piece.origin, piece.technique], " — "));
    setText("piece-description", piece.description);

    var imgs = images(piece.media);
    var hero =
      imgs.filter(function (m) {
        return m.role === "hero";
      })[0] || imgs[0];
    fillHero("piece-hero", hero, piece.name);
    fillGallery(
      "piece-gallery",
      imgs.filter(function (m) {
        return m !== hero;
      }),
      piece.name
    );
    fillLead("piece-history", piece.history);
    fillDescriptors("piece-materials", piece.materials);

    var artisanSection = byId("piece-artisan");
    var artisan = piece.artisan;
    if (artisanSection && artisan && typeof artisan.slug === "string") {
      var artisanName = artisan.artistic_name || artisan.full_name;
      var lead = artisanSection.querySelector(".section-lead");
      var link = artisanSection.querySelector(".editorial-link");
      if (lead) {
        lead.textContent = "Creada por " + artisanName + ".";
      }
      if (link) {
        link.textContent = "Creada por " + artisanName + " →";
        link.href = "/artesanos/" + encodeURIComponent(artisan.slug) + "/";
      }
      artisanSection.hidden = false;
    } else {
      setSection("piece-artisan", false);
    }

    setMetadata(
      piece.name,
      piece.description || joinNonEmpty([piece.name, piece.technique, piece.origin], " — "),
      "/piezas/" + encodeURIComponent(piece.slug) + "/"
    );
    return piece.name;
  }

  function renderPieceCards(pieces) {
    var section = byId("artisan-pieces");
    var grid = section && section.querySelector(".card-grid");
    if (!grid) {
      return;
    }
    grid.textContent = "";
    var valid = (pieces || []).filter(function (piece) {
      return piece && typeof piece.slug === "string" && typeof piece.name === "string";
    });
    valid.forEach(function (piece) {
      var cover = piece.cover_media;
      var li = document.createElement("li");
      li.className = "card";
      li.appendChild(buildImage(cover && cover.url, (cover && cover.alt_text) || piece.name));

      var title = document.createElement("p");
      title.className = "card__title";
      title.textContent = piece.name;
      li.appendChild(title);

      var action = document.createElement("a");
      action.className = "editorial-link card__action";
      action.href = "/piezas/" + encodeURIComponent(piece.slug) + "/";
      action.textContent = "Explorar pieza →";
      li.appendChild(action);

      grid.appendChild(li);
    });
    section.hidden = valid.length === 0;
  }

  function renderArtisan(artisan) {
    var name = artisan.artistic_name || artisan.full_name;
    var location = artisan.location || {};
    var techniques = artisan.techniques || [];

    setText("artisan-title", name);
    setText(
      "artisan-meta",
      joinNonEmpty(
        [joinNonEmpty([location.locality, location.state], ", "), techniques.length ? techniques[0] : ""],
        " — "
      )
    );
    setText("artisan-biography", artisan.biography);

    var imgs = images(artisan.media);
    var portrait = imgs.filter(function (m) {
      return m.role === "portrait";
    })[0];
    fillHero("artisan-hero", portrait, "Retrato de " + name);
    fillGallery(
      "artisan-gallery",
      imgs.filter(function (m) {
        return m !== portrait;
      }),
      name
    );
    fillLead("artisan-history", artisan.history);
    fillDescriptors("artisan-techniques", techniques);
    renderPieceCards(artisan.pieces);

    setMetadata(
      name,
      artisan.biography || joinNonEmpty([name, location.locality, location.state], " — "),
      "/artesanos/" + encodeURIComponent(artisan.slug) + "/"
    );
    return name;
  }

  var ENTITIES = {
    piece: {
      prefix: "/piezas/",
      fetch: function (slug) {
        return api.getPiece(slug);
      },
      render: renderPiece,
      notFound: "Esta pieza no está disponible.",
      unavailable: "No se pudo cargar la pieza. Inténtalo de nuevo en unos momentos."
    },
    artisan: {
      prefix: "/artesanos/",
      fetch: function (slug) {
        return api.getArtisan(slug);
      },
      render: renderArtisan,
      notFound: "Este perfil no está disponible.",
      unavailable: "No se pudo cargar el perfil. Inténtalo de nuevo en unos momentos."
    }
  };

  // Exactly one non-empty segment after the entity prefix (an optional
  // trailing slash is fine). Anything else is treated as "not found" without
  // asking the API.
  function readSlug(prefix) {
    var path = window.location.pathname;
    if (path.indexOf(prefix) !== 0) {
      return null;
    }
    var rest = path.slice(prefix.length);
    if (rest.charAt(rest.length - 1) === "/") {
      rest = rest.slice(0, -1);
    }
    if (!rest || rest.indexOf("/") !== -1) {
      return null;
    }
    var slug;
    try {
      slug = decodeURIComponent(rest);
    } catch (error) {
      return null;
    }
    if (!slug || slug.length > MAX_SLUG_LENGTH || /[\u0000-\u001f\u007f\/\\]/.test(slug)) {
      return null;
    }
    return slug;
  }

  function showNotFound(entity) {
    setIndexable(false);
    showState("not-found");
    announce(entity.notFound);
  }

  function showUnavailable(entity) {
    setIndexable(false);
    showState("unavailable");
    announce(entity.unavailable);
  }

  function load(entity, slug) {
    var mine = ++requestId;
    showState("loading");
    announce("Cargando…");

    var request = api && config ? entity.fetch(slug) : Promise.resolve({ status: "unavailable" });
    request.then(function (result) {
      if (mine !== requestId) {
        return;
      }
      if (result && result.status === "ok") {
        var label;
        try {
          label = entity.render(result.data);
        } catch (error) {
          showUnavailable(entity);
          return;
        }
        setIndexable(true);
        showState("content");
        announce(label ? label + " — información cargada." : "Información cargada.");
      } else if (result && result.status === "not_found") {
        showNotFound(entity);
      } else {
        showUnavailable(entity);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var entity = ENTITIES[document.body.getAttribute("data-entity")];
    if (!entity) {
      return;
    }

    var slug = readSlug(entity.prefix);
    if (slug === null) {
      showNotFound(entity);
      return;
    }

    var retry = byId("entity-retry");
    if (retry) {
      retry.addEventListener("click", function () {
        load(entity, slug);
      });
    }
    load(entity, slug);
  });
})();
