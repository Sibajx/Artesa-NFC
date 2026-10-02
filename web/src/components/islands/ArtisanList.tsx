// /artesanos — "Las manos" (2026-10): numbered editorial rows with a duotone
// portrait that takes its colour under the pointer. The list endpoint only
// returns names (API_CONTRACT.md §8), so each row loads its profile for the
// portrait, place and piece count; a row whose profile fails keeps its name.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { displayName, formatPlace } from "@/lib/format";
import { pickPortrait } from "@/lib/media";
import { artisanPath } from "@/lib/routes";
import type { Artisan, ArtisanSummary } from "@/lib/types";
import { MediaImage } from "./MediaImage";
import { LoadingView, UnavailableView } from "./StatusView";
import { useApi } from "./useApi";

// Profiles fetched for the visible list (enough for the MVP catalogue).
const PROFILE_LIMIT = 40;

export default function ArtisanList() {
  const { state, retry } = useApi(() => api().getArtisans());

  if (state.kind === "loading") return <LoadingView label="Cargando artesanos…" />;
  if (state.kind !== "ok") {
    return (
      <UnavailableView
        what="los artesanos"
        reason={state.kind === "unavailable" ? state.reason : "http_error"}
        onRetry={retry}
        level={2}
      />
    );
  }
  const artisans = state.data.data;
  if (artisans.length === 0) {
    return (
      <div className="status-view" data-state="empty">
        <p className="lead muted">Todavía no hay artesanos publicados.</p>
      </div>
    );
  }
  return <Hands artisans={artisans} />;
}

function Hands({ artisans }: { artisans: readonly ArtisanSummary[] }) {
  const [profiles, setProfiles] = useState<Record<string, Artisan>>({});

  useEffect(() => {
    let active = true;
    for (const summary of artisans.slice(0, PROFILE_LIMIT)) {
      void api()
        .getArtisan(summary.slug)
        .then((result) => {
          if (active && result.kind === "ok") {
            setProfiles((current) => ({ ...current, [summary.slug]: result.data }));
          }
        });
    }
    return () => {
      active = false;
    };
  }, [artisans]);

  return (
    <ol className="hands artisan-list">
      {artisans.map((artisan, i) => {
        const profile = profiles[artisan.slug];
        const place = formatPlace(profile?.location);
        const count = profile?.pieces.length;
        return (
          <li key={artisan.slug}>
            <a className="hands__item" href={artisanPath(artisan.slug)}>
              <span className="hands__index" aria-hidden="true">
                {String(i + 1).padStart(2, "0")}
              </span>
              <div className="hands__portrait">
                <MediaImage
                  media={profile ? pickPortrait(profile.media) : null}
                  fallbackAlt={displayName(artisan)}
                  decorative
                  sizes="(min-width: 760px) 280px, 100vw"
                />
              </div>
              <div className="hands__text">
                <h2 className="hands__name">{displayName(artisan)}</h2>
                {artisan.artistic_name && artisan.artistic_name !== artisan.full_name && (
                  <span className="hands__meta">{artisan.full_name}</span>
                )}
                {place && <span className="hud-label hands__place">{place}</span>}
                {count !== undefined && (
                  <span className="hands__meta">
                    {count === 0
                      ? "Sin piezas publicadas"
                      : `${count} pieza${count === 1 ? "" : "s"}`}
                  </span>
                )}
              </div>
              <span className="hands__arrow" aria-hidden="true">
                →
              </span>
            </a>
          </li>
        );
      })}
    </ol>
  );
}
