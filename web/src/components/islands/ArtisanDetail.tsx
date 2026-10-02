// /artesanos/{slug} — artisan profile (DESIGN_SYSTEM.md §10) in the neutral
// shell (F-08). public_contact has no key contract, so it is not rendered.
import { useEffect } from "react";
import { api } from "@/lib/api";
import { displayName, excerpt, formatPlace } from "@/lib/format";
import { imagesWithRoles, pickPortrait } from "@/lib/media";
import { ARTISAN_PREFIX, slugFromPath } from "@/lib/routes";
import { setIndexable, setMetadata } from "@/lib/seo";
import type { Artisan } from "@/lib/types";
import { MediaImage } from "./MediaImage";
import { PieceCard } from "./PieceCard";
import { Story } from "./Story";
import { LoadingView, NotFoundView, UnavailableView } from "./StatusView";
import { useApi } from "./useApi";
import { useCountUp } from "./useCountUp";
import { useDarkZone } from "./useDarkZone";

const NOT_FOUND = { kind: "not_found" } as const;

export default function ArtisanDetail() {
  const slug = slugFromPath(window.location.pathname, ARTISAN_PREFIX);
  const { state, retry } = useApi(() =>
    slug === null ? Promise.resolve(NOT_FOUND) : api().getArtisan(slug),
  );

  useEffect(() => {
    if (state.kind === "loading") return;
    if (state.kind === "ok") {
      const artisan = state.data;
      setIndexable(true);
      setMetadata(
        displayName(artisan),
        artisan.biography ?? `${displayName(artisan)}, artesano en ArtesaNFC.`,
        `${ARTISAN_PREFIX}${encodeURIComponent(artisan.slug)}/`,
      );
    } else {
      setIndexable(false);
    }
  }, [state]);

  if (state.kind === "loading") return <LoadingView label="Cargando perfil…" />;
  if (state.kind === "not_found") {
    return (
      <NotFoundView
        title="Perfil no disponible"
        body="Este perfil no existe o no está publicado."
        backHref="/artesanos/"
        backLabel="Ver todos los artesanos"
      />
    );
  }
  if (state.kind === "unavailable") {
    return <UnavailableView what="el perfil" reason={state.reason} onRetry={retry} />;
  }
  return <ArtisanView artisan={state.data} />;
}

function Stat({ value, label }: { value: number; label: string }) {
  const shown = useCountUp(value);
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        <span className="sr-only">{value}</span>
        <span aria-hidden="true">{shown}</span>
      </dd>
    </div>
  );
}

// "Retrato" (2026-10): a dark hero where the portrait is read by a scan line
// inside a HUD frame; the history as an editorial quote; the workshop as a
// masonry; the pieces in a lit vitrine grid.
function ArtisanView({ artisan }: { artisan: Artisan }) {
  const name = displayName(artisan);
  const portrait = pickPortrait(artisan.media);
  const gallery = imagesWithRoles(artisan.media, ["gallery", "process", "detail"], portrait);
  const place = formatPlace(artisan.location);
  const bio = artisan.biography ? excerpt(artisan.biography) : null;
  useDarkZone(true);

  return (
    <article className="artisan" aria-labelledby="artisan-title">
      <header className="night-hero on-dark portrait-hero" data-header-dark-zone>
        <div className="night-hero__grid" aria-hidden="true"></div>
        <div className="container portrait-hero__grid">
          <figure className="portrait-hero__frame artisan__portrait">
            <MediaImage media={portrait} fallbackAlt={name} loading="eager" />
            <span className="hud-frame" aria-hidden="true"></span>
          </figure>
          <div className="portrait-hero__text artisan__heading rise">
            <p className="hud-label">{place ?? "Artesano"}</p>
            <h1 id="artisan-title" className="display-l">
              {name}
            </h1>
            {artisan.artistic_name && artisan.artistic_name !== artisan.full_name && (
              <p className="muted">{artisan.full_name}</p>
            )}
            {bio && <p className="lead">{bio.text}</p>}
            {bio?.truncated && (
              <a className="editorial-link hero-more" href="#biografia">
                Leer biografía completa
              </a>
            )}
            <dl className="stats">
              <Stat value={artisan.pieces.length} label="Piezas" />
              {artisan.techniques.length > 0 && (
                <Stat value={artisan.techniques.length} label="Técnicas" />
              )}
              {artisan.languages.length > 0 && (
                <Stat value={artisan.languages.length} label="Lenguas" />
              )}
            </dl>
            {artisan.techniques.length > 0 && (
              <ul className="craft-tags" aria-label="Técnicas">
                {artisan.techniques.map((t) => (
                  <li key={t}>{t.replace(/[.]$/, "")}</li>
                ))}
              </ul>
            )}
            {artisan.languages.length > 0 && (
              <p className="muted">Lenguas: {artisan.languages.join(", ")}</p>
            )}
          </div>
        </div>
      </header>

      {bio?.truncated && artisan.biography && (
        <Story id="biografia" title="Biografía" text={artisan.biography} quote={false} />
      )}

      {artisan.history && <Story id="historia" title="Historia" text={artisan.history} />}

      {gallery.length > 0 && (
        <section className="section container" aria-labelledby="artisan-gallery-title">
          <h2 id="artisan-gallery-title" className="heading-2">
            Taller y proceso
          </h2>
          <ul className="masonry" role="list">
            {gallery.map((media) => (
              <li key={`${media.position}-${media.url}`} className="media-frame">
                <MediaImage media={media} fallbackAlt={name} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="section vitrine-page" aria-labelledby="artisan-pieces-title">
        <div className="container">
          <p className="hud-label">Colección</p>
          <h2 id="artisan-pieces-title" className="heading-1">
            Piezas de {name}
          </h2>
          {artisan.pieces.length > 0 ? (
            <ul className="piece-grid" role="list">
              {artisan.pieces.map((piece) => (
                <li key={piece.slug}>
                  <PieceCard piece={piece} headingLevel={3} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Todavía no hay piezas publicadas de {name}.</p>
          )}
        </div>
      </section>
    </article>
  );
}
