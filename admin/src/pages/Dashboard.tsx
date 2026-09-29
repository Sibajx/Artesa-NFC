import { Link } from 'react-router-dom';
import { adminApi } from '../api';
import type { PublicationStatus } from '../api';
import { formatDateTime } from '../format';
import { useLoad } from '../hooks';
import { ErrorState, Loading, PageHeader } from '../ui';

function countBy(items: { publication_status: PublicationStatus }[]) {
  const counts = { draft: 0, published: 0, archived: 0 };
  for (const item of items) counts[item.publication_status] += 1;
  return counts;
}

export default function Dashboard() {
  const state = useLoad('dashboard', async (signal) => {
    const [artisans, pieces, events] = await Promise.all([
      adminApi.artisans({}, signal),
      adminApi.pieces({}, signal),
      adminApi.auditEvents({ limit: '8' }, signal),
    ]);
    return { artisans: artisans.data, pieces: pieces.data, events: events.data };
  });

  return (
    <div className="max-w-6xl mx-auto pb-12">
      <PageHeader title="Resumen" subtitle="Estado del contenido de ArtesaNFC." />

      {state.status === 'loading' && <Loading label="Cargando resumen..." />}
      {state.status === 'error' && <div className="card-elevated"><ErrorState error={state.error} /></div>}
      {state.status === 'ready' && (() => {
        const artisans = countBy(state.data.artisans);
        const pieces = countBy(state.data.pieces);
        const metrics = [
          { label: 'Artesanos', value: state.data.artisans.length, detail: `${artisans.published} publicados · ${artisans.draft} borradores`, to: '/artesanos' },
          { label: 'Piezas', value: state.data.pieces.length, detail: `${pieces.published} publicadas · ${pieces.draft} borradores`, to: '/piezas' },
          { label: 'Archivados', value: artisans.archived + pieces.archived, detail: `${artisans.archived} artesanos · ${pieces.archived} piezas`, to: '/piezas' },
        ];
        return (
          <>
            <section aria-label="Métricas" className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
              {metrics.map((metric) => (
                <Link key={metric.label} to={metric.to} className="bg-white p-6 rounded-xl border border-botanica-gris/15 shadow-sm flex flex-col hover:border-botanica-jade/40">
                  <h2 className="text-botanica-gris font-medium text-sm mb-4">{metric.label}</h2>
                  <div className="text-4xl font-serif text-botanica-negro mb-2 tracking-tight tabular-nums">{metric.value}</div>
                  <div className="text-sm mt-auto text-botanica-grafito/70">{metric.detail}</div>
                </Link>
              ))}
            </section>

            <section>
              <h2 className="text-2xl font-serif text-botanica-negro mb-6">Actividad reciente</h2>
              <div className="bg-white border border-botanica-gris/15 rounded-xl overflow-hidden shadow-sm">
                {state.data.events.length === 0 ? (
                  <p className="p-12 text-center text-botanica-gris">
                    Aún no hay eventos de auditoría. Aparecerán cuando se habiliten las altas y ediciones (fase 2).
                  </p>
                ) : (
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="table-header text-botanica-gris text-sm">
                        <th scope="col" className="py-4 px-6 font-medium">Acción</th>
                        <th scope="col" className="py-4 px-6 font-medium hidden md:table-cell">Usuario</th>
                        <th scope="col" className="py-4 px-6 font-medium text-right">Fecha</th>
                      </tr>
                    </thead>
                    <tbody className="text-botanica-grafito text-sm">
                      {state.data.events.map((event) => (
                        <tr key={event.id} className="border-b border-botanica-gris/10 last:border-0">
                          <td className="py-4 px-6 font-medium text-botanica-negro font-mono text-xs">{event.action}</td>
                          <td className="py-4 px-6 hidden md:table-cell">{event.actor_email ?? event.actor_type}</td>
                          <td className="py-4 px-6 text-right text-botanica-gris">{formatDateTime(event.occurred_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </section>
          </>
        );
      })()}
    </div>
  );
}
