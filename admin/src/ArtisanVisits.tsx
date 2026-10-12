import { Link } from 'react-router-dom';
import { adminApi } from './api';
import { formatDate } from './format';
import { useLoad } from './hooks';

// The visits recorded for one registered artisan, on their page. Only people with the
// Visits permission see it (the API checks it too); the page that edits them is Visitas.
export function ArtisanVisits({ artisanId }: { artisanId: string }) {
  const state = useLoad(`artisan-visits:${artisanId}`, (signal) => adminApi.visits(artisanId, signal));
  if (state.status === 'error') return null; // an extra block: it never gets in the way of the page
  const visits = state.status === 'ready' ? state.data.data : [];
  return (
    <section aria-labelledby="artisan-visits-heading" className="bg-white rounded-xl border border-botanica-gris/15 p-6 shadow-sm">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="artisan-visits-heading" className="text-xl font-serif text-botanica-negro">Visitas</h2>
        <Link to="/visitas" className="text-sm text-botanica-jade hover:underline">Ir a Visitas</Link>
      </div>
      {state.status === 'loading' && <p className="text-sm text-botanica-gris">Cargando visitas…</p>}
      {state.status === 'ready' && visits.length === 0 && (
        <p className="text-sm text-botanica-gris">Todavía no hay visitas registradas de este artesano.</p>
      )}
      {visits.length > 0 && (
        <ul className="flex flex-col gap-3">
          {visits.map((v) => (
            <li key={v.id} className="text-sm text-botanica-grafito">
              <span className="font-medium text-botanica-negro">{formatDate(v.visited_on)}</span>
              {v.place && <span className="text-botanica-gris"> · {v.place}</span>}
              <p className="mt-0.5 line-clamp-2">{v.summary}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
