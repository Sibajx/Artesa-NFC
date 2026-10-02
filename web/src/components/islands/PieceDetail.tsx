// /piezas/{slug} — piece page and public digital passport, rendered inside
// the neutral shell (F-08): nothing about the piece exists in the HTML until
// the public API answers 200 for this slug.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { availabilityLabel, displayName, excerpt, formatPlace } from "@/lib/format";
import { imagesWithRoles, pickHeroImage, pickModel, pickPortrait } from "@/lib/media";
import { artisanPath, PIECE_PREFIX, slugFromPath } from "@/lib/routes";
import { setIndexable, setMetadata } from "@/lib/seo";
import type { Artisan, Piece } from "@/lib/types";
import { MediaImage } from "./MediaImage";
import { PassportPanel } from "./PassportPanel";
import { PieceCard } from "./PieceCard";
import { PieceViewer } from "./PieceViewer";
import { Story } from "./Story";
import { LoadingView, NotFoundView, UnavailableView } from "./StatusView";
import { useApi } from "./useApi";
import { useDarkZone } from "./useDarkZone";

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

  useDarkZone(true);
  const availabilityKey = String(piece.availability_status);
  const availability = availabilityLabel(piece.availability_status);
  const summary = piece.description ? excerpt(piece.description) : null;

  // "Expediente" (2026-10): the piece in a lit vitrine with a rotating seal,
  // its holographic passport, a film strip of photos, its story as a quote
  // and the maker's card.
  return (
    <article className="piece dossier" aria-labelledby="piece-title">
      <header className="night-hero on-dark dossier-hero" data-header-dark-zone>
        <div className="night-hero__grid" aria-hidden="true"></div>
        <div className="container dossier-hero__grid piece__intro">
          <div className="vitrine piece__media">
            {model ? (
              <PieceViewer model={model} poster={hero} name={piece.name} />
            ) : (
              <div className="piece__photo media-frame">
                <MediaImage media={hero} fallbackAlt={piece.name} loading="eager" />
              </div>
            )}
            <span className="hud-frame" aria-hidden="true"></span>
            <svg
              className="vitrine__seal"
              viewBox="0 0 100 100"
              aria-hidden="true"
              focusable="false"
            >
              <defs>
                <path
                  id="piece-seal-path"
                  d="M50 50m-36 0a36 36 0 1 1 72 0a36 36 0 1 1-72 0"
                ></path>
              </defs>
              <circle cx="50" cy="50" r="48"></circle>
              <g className="vitrine__seal-text">
                <text>
                  <textPath href="#piece-seal-path" textLength="226" lengthAdjust="spacing">
                    {`PIEZA ÚNICA · ${piece.public_code} · `}
                  </textPath>
                </text>
              </g>
              <path d="M44 42a10 10 0 0 1 0 16M50 37a17 17 0 0 1 0 26M56 32a24 24 0 0 1 0 36"></path>
            </svg>
          </div>
          <div className="dossier-hero__text piece__heading rise">
            <p className="hud-label">{[artisanName, place].filter(Boolean).join(" · ")}</p>
            <h1 id="piece-title" className="display-l">
              {piece.name}
            </h1>
            {availability && (
              <span className={`chip chip--${availabilityKey}`}>{availability}</span>
            )}
            {summary && <p className="lead">{summary.text}</p>}
            {summary?.truncated && (
              <a className="editorial-link hero-more" href="#sobre-la-pieza">
                Leer descripción completa
              </a>
            )}
            <a className="editorial-link" href={artisanPath(piece.artisan.slug)}>
              Creada por {artisanName}
            </a>
          </div>
        </div>
      </header>

      <div className="container piece__passport section">
        <PassportPanel piece={piece} artisan={artisan} />
      </div>

      {gallery.length > 0 && (
        <section className="container" aria-labelledby="piece-gallery-title">
          <h2 id="piece-gallery-title" className="heading-2">
            Fotografías y detalles
          </h2>
          {/* eslint-disable jsx-a11y/no-noninteractive-tabindex -- a horizontally scrollable region must be keyboard reachable (WCAG 2.1.1) */}
          <div
            className="film-wrap"
            role="region"
            tabIndex={0}
            aria-label="Fotografías de la pieza (desplazable)"
          >
            <ul className="film" role="list">
              {gallery.map((media) => (
                <li key={`${media.position}-${media.url}`}>
                  <div className="media-frame">
                    <MediaImage
                      media={media}
                      fallbackAlt={piece.name}
                      sizes="(min-width: 700px) 520px, 78vw"
                    />
                  </div>
                </li>
              ))}
            </ul>
          </div>
          {/* eslint-enable jsx-a11y/no-noninteractive-tabindex */}
        </section>
      )}

      {summary?.truncated && piece.description && (
        <Story id="sobre-la-pieza" title="Sobre la pieza" text={piece.description} quote={false} />
      )}

      {piece.history && <Story id="historia" title="Historia" text={piece.history} />}

      {process.length > 0 && (
        <section className="section container" aria-labelledby="piece-process-title">
          <h2 id="piece-process-title" className="heading-2">
            Proceso
          </h2>
          <ul className="masonry" role="list" aria-label="Fotografías del proceso">
            {process.map((media) => (
              <li key={`${media.position}-${media.url}`} className="media-frame">
                <MediaImage media={media} fallbackAlt={`Proceso de ${piece.name}`} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {artisan && (
        <section className="section container" aria-labelledby="piece-artisan-title">
          <div className="maker">
            <div className="maker__portrait">
              <MediaImage media={pickPortrait(artisan.media)} fallbackAlt={displayName(artisan)} />
            </div>
            <div className="maker__text">
              <p className="hud-label light-hud">Hecha por</p>
              <h2 id="piece-artisan-title" className="heading-1">
                {displayName(artisan)}
              </h2>
              {formatPlace(artisan.location) && (
                <p className="muted">{formatPlace(artisan.location)}</p>
              )}
              {artisan.biography && <p>{excerpt(artisan.biography, 220).text}</p>}
              <a className="button" href={artisanPath(artisan.slug)}>
                Conocer a {displayName(artisan)}
              </a>
            </div>
          </div>
        </section>
      )}

      {related.length > 0 && (
        <section className="section vitrine-page" aria-labelledby="piece-related-title">
          <div className="container">
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
          </div>
        </section>
      )}
    </article>
  );
}
