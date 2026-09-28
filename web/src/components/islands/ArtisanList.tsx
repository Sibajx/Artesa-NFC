// /artesanos — artisans list (API_CONTRACT.md §8: summaries only).
import { api } from "@/lib/api";
import { displayName } from "@/lib/format";
import { artisanPath } from "@/lib/routes";
import { LoadingView, UnavailableView } from "./StatusView";
import { useApi } from "./useApi";

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
  return (
    <ul className="artisan-list" role="list">
      {artisans.map((artisan) => (
        <li key={artisan.slug}>
          <a className="artisan-list__link" href={artisanPath(artisan.slug)}>
            <h2 className="heading-1">{displayName(artisan)}</h2>
            {artisan.artistic_name && artisan.artistic_name !== artisan.full_name && (
              <span className="muted">{artisan.full_name}</span>
            )}
            <span className="editorial-link" aria-hidden="true">
              Ver perfil
            </span>
          </a>
        </li>
      ))}
    </ul>
  );
}
