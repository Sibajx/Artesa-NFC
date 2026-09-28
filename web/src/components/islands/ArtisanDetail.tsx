// /artesanos/{slug} — artisan profile (DESIGN_SYSTEM.md §10) in the neutral
// shell (F-08). public_contact has no key contract, so it is not rendered.
import { useEffect } from "react";
import { api } from "@/lib/api";
import { displayName, formatPlace } from "@/lib/format";
import { imagesWithRoles, pickPortrait } from "@/lib/media";
import { ARTISAN_PREFIX, slugFromPath } from "@/lib/routes";
import { setIndexable, setMetadata } from "@/lib/seo";
import type { Artisan } from "@/lib/types";
import { MediaImage } from "./MediaImage";
import { PieceCard } from "./PieceCard";
import { LoadingView, NotFoundView, UnavailableView } from "./StatusView";
import { useApi } from "./useApi";

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

function ArtisanView({ artisan }: { artisan: Artisan }) {
  const name = displayName(artisan);
  const portrait = pickPortrait(artisan.media);
  const gallery = imagesWithRoles(artisan.media, ["gallery", "process", "detail"], portrait);
  const place = formatPlace(artisan.location);

  return (
    <article className="artisan" aria-labelledby="artisan-title">
      <div className="container artisan__intro">
        <div className="artisan__portrait media-frame">
          <MediaImage media={portrait} fallbackAlt={name} loading="eager" />
        </div>
        <div className="artisan__heading">
          <p className="eyebrow">{place ?? "Artesano"}</p>
          <h1 id="artisan-title" className="display-l">
            {name}
          </h1>
          {artisan.artistic_name && artisan.artistic_name !== artisan.full_name && (
            <p className="muted">{artisan.full_name}</p>
          )}
          {artisan.biography && <p className="lead">{artisan.biography}</p>}
          {(artisan.techniques.length > 0 || artisan.languages.length > 0) && (
            <dl className="facts">
              {artisan.techniques.length > 0 && (
                <div>
                  <dt>Técnicas</dt>
                  <dd>{artisan.techniques.join(", ")}</dd>
                </div>
              )}
              {artisan.languages.length > 0 && (
                <div>
                  <dt>Lenguas</dt>
                  <dd>{artisan.languages.join(", ")}</dd>
                </div>
              )}
            </dl>
          )}
        </div>
      </div>

      {artisan.history && (
        <section className="section piece__story" aria-labelledby="artisan-history-title">
          <div className="container piece__story-grid">
            <h2 id="artisan-history-title" className="heading-1">
              Historia
            </h2>
            <p className="lead">{artisan.history}</p>
          </div>
        </section>
      )}

      {gallery.length > 0 && (
        <section className="section container" aria-labelledby="artisan-gallery-title">
          <h2 id="artisan-gallery-title" className="heading-2">
            Taller y proceso
          </h2>
          <ul className="photo-grid" role="list">
            {gallery.map((media) => (
              <li key={`${media.position}-${media.url}`} className="media-frame">
                <MediaImage media={media} fallbackAlt={name} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="section container" aria-labelledby="artisan-pieces-title">
        <h2 id="artisan-pieces-title" className="heading-2">
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
      </section>
    </article>
  );
}
