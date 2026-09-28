// /piezas/{slug} — piece page and public digital passport, rendered inside
// the neutral shell (F-08): nothing about the piece exists in the HTML until
// the public API answers 200 for this slug.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { displayName, formatPlace } from "@/lib/format";
import { imagesWithRoles, pickHeroImage, pickModel, pickPortrait } from "@/lib/media";
import { artisanPath, PIECE_PREFIX, slugFromPath } from "@/lib/routes";
import { setIndexable, setMetadata } from "@/lib/seo";
import type { Artisan, Piece } from "@/lib/types";
import { MediaImage } from "./MediaImage";
import { PassportPanel } from "./PassportPanel";
import { PieceCard } from "./PieceCard";
import { PieceViewer } from "./PieceViewer";
import { LoadingView, NotFoundView, UnavailableView } from "./StatusView";
import { useApi } from "./useApi";

const NOT_FOUND = { kind: "not_found" } as const;

export default function PieceDetail() {
  const slug = slugFromPath(window.location.pathname, PIECE_PREFIX);
  const { state, retry } = useApi(() =>
    slug === null ? Promise.resolve(NOT_FOUND) : api().getPiece(slug),
  );

  useEffect(() => {
    if (state.kind === "loading") return;
    if (state.kind === "ok") {
      const piece = state.data;
      setIndexable(true);
      setMetadata(
        piece.name,
        piece.description ?? `${piece.name}, pieza de ${displayName(piece.artisan)}.`,
        `${PIECE_PREFIX}${encodeURIComponent(piece.slug)}/`,
      );
    } else {
      setIndexable(false);
    }
  }, [state]);

  if (state.kind === "loading") return <LoadingView label="Cargando pieza…" />;
  if (state.kind === "not_found") {
    return (
      <NotFoundView
        title="Pieza no disponible"
        body="Esta pieza no existe o no está publicada."
        backHref="/piezas/"
        backLabel="Ver todas las piezas"
      />
    );
  }
  if (state.kind === "unavailable") {
    return <UnavailableView what="la pieza" reason={state.reason} onRetry={retry} />;
  }
  return <PieceView piece={state.data} />;
}

export function PieceView({ piece }: { piece: Piece }) {
  const [artisan, setArtisan] = useState<Artisan | null>(null);

  // Secondary: community, region, portrait and related pieces. If it fails,
  // those parts are simply omitted; the piece page stands on its own.
  useEffect(() => {
    let active = true;
    void api()
      .getArtisan(piece.artisan.slug)
      .then((result) => {
        if (active && result.kind === "ok") setArtisan(result.data);
      });
    return () => {
      active = false;
    };
  }, [piece.artisan.slug]);

  const hero = pickHeroImage(piece.media);
  const model = pickModel(piece.media);
  const gallery = imagesWithRoles(piece.media, ["gallery", "detail"], hero);
  const process = imagesWithRoles(piece.media, ["process"], hero);
  const place = formatPlace(artisan?.location) ?? piece.origin;
  const related = artisan ? artisan.pieces.filter((p) => p.slug !== piece.slug) : [];
  const artisanName = displayName(piece.artisan);

  return (
    <article className="piece" aria-labelledby="piece-title">
      <div className="piece__intro container">
        <div className="piece__media">
          {model ? (
            <PieceViewer model={model} poster={hero} name={piece.name} />
          ) : (
            <div className="piece__photo media-frame">
              <MediaImage media={hero} fallbackAlt={piece.name} loading="eager" />
            </div>
          )}
        </div>
        <div className="piece__heading">
          <p className="eyebrow">{[artisanName, place].filter(Boolean).join(" · ")}</p>
          <h1 id="piece-title" className="display-l">
            {piece.name}
          </h1>
          {piece.description && <p className="lead">{piece.description}</p>}
          <a className="editorial-link" href={artisanPath(piece.artisan.slug)}>
            Creada por {artisanName}
          </a>
        </div>
      </div>

      <div className="container piece__passport">
        <PassportPanel piece={piece} artisan={artisan} />
      </div>

      {gallery.length > 0 && (
        <section className="section container" aria-labelledby="piece-gallery-title">
          <h2 id="piece-gallery-title" className="heading-2">
            Fotografías y detalles
          </h2>
          <ul className="photo-grid" role="list">
            {gallery.map((media) => (
              <li key={`${media.position}-${media.url}`} className="media-frame">
                <MediaImage
                  media={media}
                  fallbackAlt={piece.name}
                  sizes="(min-width: 900px) 33vw, 50vw"
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      {(piece.history || process.length > 0) && (
        <section className="section piece__story" aria-labelledby="piece-story-title">
          <div className="container piece__story-grid">
            <h2 id="piece-story-title" className="heading-1">
              {piece.history ? "Historia" : "Proceso"}
            </h2>
            <div className="piece__story-body">
              {piece.history && <p className="lead">{piece.history}</p>}
              {process.length > 0 && (
                <ul
                  className="photo-grid photo-grid--process"
                  role="list"
                  aria-label="Fotografías del proceso"
                >
                  {process.map((media) => (
                    <li key={`${media.position}-${media.url}`} className="media-frame">
                      <MediaImage media={media} fallbackAlt={`Proceso de ${piece.name}`} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </section>
      )}

      {artisan && (
        <section className="section container artisan-strip" aria-labelledby="piece-artisan-title">
          <div className="artisan-strip__portrait media-frame">
            <MediaImage media={pickPortrait(artisan.media)} fallbackAlt={displayName(artisan)} />
          </div>
          <div className="artisan-strip__text">
            <p className="eyebrow">Artesano</p>
            <h2 id="piece-artisan-title" className="heading-1">
              {displayName(artisan)}
            </h2>
            {formatPlace(artisan.location) && (
              <p className="muted">{formatPlace(artisan.location)}</p>
            )}
            {artisan.biography && <p className="lead">{artisan.biography}</p>}
            <a className="editorial-link" href={artisanPath(artisan.slug)}>
              Conocer a {displayName(artisan)}
            </a>
          </div>
        </section>
      )}

      {related.length > 0 && (
        <section className="section container" aria-labelledby="piece-related-title">
          <h2 id="piece-related-title" className="heading-2">
            Otras piezas de {artisanName}
          </h2>
          <ul className="piece-grid" role="list">
            {related.map((p) => (
              <li key={p.slug}>
                <PieceCard piece={p} headingLevel={3} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
