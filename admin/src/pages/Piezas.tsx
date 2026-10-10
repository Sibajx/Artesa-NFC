import { Link, useSearchParams } from 'react-router-dom';
import { adminApi, PIECES_CSV_URL } from '../api';
import { formatDate, labels } from '../format';
import { useListFilters, useLoad } from '../hooks';
import { ListFilters } from '../ListFilters';
import { Can, ErrorState, Loading, PageHeader, PublicationBadge } from '../ui';
import { QuickPublish } from '../QuickPublish';
import { useState } from 'react';

export default function Piezas() {
  const filters = useListFilters();
  const [revision, setRevision] = useState(0);
  const [params] = useSearchParams();
  const artisanId = params.get('artesano') ?? '';
  const state = useLoad(`pieces:${revision}:${filters.status}:${filters.q}:${artisanId}`, (signal) =>
    adminApi.pieces(
      { publication_status: filters.status || undefined, q: filters.q || undefined, artisan_id: artisanId || undefined },
      signal,
    ),
  );

  return (
    <div className="max-w-6xl mx-auto pb-12">
      <PageHeader title="Piezas" subtitle="Inventario completo, con su estado de publicación y disponibilidad.">
        <div className="flex flex-wrap gap-3">
          <a href={PIECES_CSV_URL} download className="btn-secondary" title="Todas las piezas fuera de la papelera, sin filtros. Si tienes el rol de custodio incluye la certificación.">Exportar CSV</a>
          <Can permission="edit">
          <Link to={artisanId ? `/piezas/nueva?artesano=${artisanId}` : '/piezas/nueva'} className="btn-primary"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="M12 5v14"/></svg>Nueva pieza</Link>
          </Can>
        </div>
      </PageHeader>

      <div className="bg-white rounded-xl border border-botanica-gris/20 overflow-hidden shadow-sm">
        <ListFilters {...filters} searchLabel="Buscar por nombre o código" />
        {state.status === 'loading' && <Loading label="Cargando piezas..." />}
        {state.status === 'error' && <ErrorState error={state.error} />}
        {state.status === 'ready' && (state.data.data.length === 0 ? (
          <p className="p-12 text-center text-botanica-gris">
            {filters.status || filters.q || artisanId ? 'Ninguna pieza coincide con los filtros.' : 'Todavía no hay piezas registradas.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <caption className="sr-only">Piezas ({state.data.meta.total})</caption>
              <thead>
                <tr className="table-header text-sm text-botanica-gris">
                  <th scope="col" className="py-4 px-6 font-medium">Pieza</th>
                  <th scope="col" className="py-4 px-6 font-medium">Código</th>
                  <th scope="col" className="py-4 px-6 font-medium">Artesano</th>
                  <th scope="col" className="py-4 px-6 font-medium">Publicación</th>
                  <th scope="col" className="py-4 px-6 font-medium">Disponibilidad</th>
                  <th scope="col" className="py-4 px-6 font-medium">Actualizada</th>
                  <th scope="col" className="py-4 px-6 font-medium"><span className="sr-only">Acciones</span></th>
                </tr>
              </thead>
              <tbody className="text-sm text-botanica-grafito">
                {state.data.data.map((piece) => (
                  <tr key={piece.id} className="border-b border-botanica-gris/10 last:border-0 table-row-hover">
                    <td className="py-4 px-6 font-medium">
                      <Link to={`/piezas/${piece.id}`} className="text-botanica-negro hover:text-botanica-jade">
                        {piece.name}
                      </Link>
                    </td>
                    <td className="py-4 px-6 font-mono text-xs">{piece.public_code}</td>
                    <td className="py-4 px-6">
                      <Link to={`/artesanos/${piece.artisan_id}`} className="hover:text-botanica-jade">{piece.artisan_slug}</Link>
                    </td>
                    <td className="py-4 px-6"><PublicationBadge status={piece.publication_status} /></td>
                    <td className="py-4 px-6">{labels.availability(piece.availability_status)}</td>
                    <td className="py-4 px-6">{formatDate(piece.updated_at)}</td>
                    <td className="py-4 px-6 text-right"><QuickPublish kind="pieces" id={piece.id} version={piece.updated_at} name={piece.name} status={piece.publication_status} onChanged={() => setRevision((r) => r + 1)} /></td>
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
