import { Link } from 'react-router-dom';
import { adminApi } from '../api';
import { formatDate } from '../format';
import { useListFilters, useLoad } from '../hooks';
import { ListFilters } from '../ListFilters';
import { ErrorState, Loading, PageHeader, PublicationBadge } from '../ui';

export default function Artesanos() {
  const filters = useListFilters();
  const state = useLoad(`artisans:${filters.status}:${filters.q}`, (signal) =>
    adminApi.artisans({ publication_status: filters.status || undefined, q: filters.q || undefined }, signal),
  );

  return (
    <div className="max-w-6xl mx-auto pb-12">
      <PageHeader title="Artesanos" subtitle="Todos los perfiles, publicados o no." />

      <div className="bg-white rounded-xl border border-botanica-gris/20 overflow-hidden shadow-sm">
        <ListFilters {...filters} searchLabel="Buscar por nombre" />
        {state.status === 'loading' && <Loading label="Cargando artesanos..." />}
        {state.status === 'error' && <ErrorState error={state.error} />}
        {state.status === 'ready' && (state.data.data.length === 0 ? (
          <p className="p-12 text-center text-botanica-gris">
            {filters.status || filters.q ? 'Ningún artesano coincide con los filtros.' : 'Todavía no hay artesanos registrados.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <caption className="sr-only">Artesanos ({state.data.meta.total})</caption>
              <thead>
                <tr className="table-header text-sm text-botanica-gris">
                  <th scope="col" className="py-4 px-6 font-medium">Nombre</th>
                  <th scope="col" className="py-4 px-6 font-medium">Nombre artístico</th>
                  <th scope="col" className="py-4 px-6 font-medium">Piezas</th>
                  <th scope="col" className="py-4 px-6 font-medium">Estado</th>
                  <th scope="col" className="py-4 px-6 font-medium">Actualizado</th>
                </tr>
              </thead>
              <tbody className="text-sm text-botanica-grafito">
                {state.data.data.map((artisan) => (
                  <tr key={artisan.id} className="border-b border-botanica-gris/10 last:border-0 table-row-hover">
                    <td className="py-4 px-6 font-medium">
                      <Link to={`/artesanos/${artisan.id}`} className="text-botanica-negro hover:text-botanica-jade">
                        {artisan.full_name}
                      </Link>
                    </td>
                    <td className="py-4 px-6">{artisan.artistic_name ?? '—'}</td>
                    <td className="py-4 px-6 tabular-nums">{artisan.piece_count}</td>
                    <td className="py-4 px-6"><PublicationBadge status={artisan.publication_status} /></td>
                    <td className="py-4 px-6">{formatDate(artisan.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
