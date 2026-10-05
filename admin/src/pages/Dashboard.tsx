import { Link } from 'react-router-dom';
import { adminApi } from '../api';
import type { PublicationStatus, Summary, SummaryBucket } from '../api';
import { actionLabel, actorLabel } from '../audit-labels';
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
    const [artisans, pieces, events, summary] = await Promise.all([
      adminApi.artisans({}, signal),
      adminApi.pieces({}, signal),
      adminApi.auditEvents({ limit: '8' }, signal),
      adminApi.summary(signal),
    ]);
    return { artisans: artisans.data, pieces: pieces.data, events: events.data, summary };
  });

  return (
    <div className="max-w-6xl mx-auto pb-12">
      <PageHeader title="Resumen" subtitle="Lo que hay y lo que falta en ArtesaNFC." />

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

            <Pending summary={state.data.summary} />

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
                          <td className="py-4 px-6 font-medium text-botanica-negro">{actionLabel(event.action)}</td>
                          <td className="py-4 px-6 hidden md:table-cell">{actorLabel(event.actor_email, event.actor_type)}</td>
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


// P-026 G5/G8: what needs attention. Each card links to where it is fixed;
// cards for other roles simply do not come back from the API.
const CARDS: { key: keyof Summary; title: string; hint: string; to: (id: string) => string }[] = [
  { key: 'published_without_certificate', title: 'Publicadas sin certificado', hint: 'Emitir el certificado y grabar el chip', to: (id) => `/certificacion/${id}` },
  { key: 'certified_without_chip', title: 'Certificadas sin chip grabado', hint: 'Grabar el chip desde el Android', to: (id) => `/certificacion/${id}` },
  { key: 'certified_without_card', title: 'Sin tarjeta del comprador', hint: 'Generar e imprimir la tarjeta', to: (id) => `/certificacion/${id}` },
  { key: 'designs_with_changes_requested', title: 'Diseños con cambios pedidos', hint: 'El artesano pidió cambios', to: (id) => `/diseno/${id}` },
  { key: 'designs_in_review', title: 'Diseños esperando al artesano', hint: 'Enviados por WhatsApp', to: (id) => `/diseno/${id}` },
  { key: 'designs_to_publish', title: 'Diseños aprobados sin publicar', hint: 'Listos para publicar', to: (id) => `/diseno/${id}` },
  { key: 'authorizations_with_changes_requested', title: 'Artesanos que pidieron cambios', hint: 'Corregir y mandar un enlace nuevo', to: (id) => `/artesanos/${id}` },
  { key: 'published_artisans_without_authorization', title: 'Artesanos publicados sin autorización registrada', hint: 'Pedirla por WhatsApp', to: (id) => `/artesanos/${id}` },
  { key: 'authorizations_waiting', title: 'Autorizaciones esperando respuesta', hint: 'Enlace enviado', to: (id) => `/artesanos/${id}` },
  { key: 'cards_blocked_or_locked', title: 'Tarjetas bloqueadas', hint: 'Revisar con el dueño', to: (id) => `/certificacion/${id}` },
  { key: 'reported_stolen', title: 'Piezas reportadas como robadas', hint: '', to: (id) => `/certificacion/${id}` },
];

function Pending({ summary }: { summary: Summary }) {
  const cards = CARDS.filter((c) => {
    const bucket = summary[c.key] as SummaryBucket | undefined;
    return bucket && bucket.count > 0;
  });
  const sales = summary.sales_last_30_days;
  return (
    <section aria-labelledby="pending-heading" className="mb-12 flex flex-col gap-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="pending-heading" className="text-2xl font-serif text-botanica-negro">Pendientes</h2>
        <p className="text-sm text-botanica-grafito">
          Ventas en 30 días: <strong>{sales.count}</strong>
          {sales.count > 0 && <> · {(sales.total_cents / 100).toLocaleString('es-MX', { style: 'currency', currency: 'MXN' })}</>}
        </p>
      </div>

      {summary.recent_answers.length > 0 && (
        <div className="bg-white border border-botanica-gris/15 rounded-xl p-5 shadow-sm">
          <h3 className="text-sm font-medium text-botanica-gris mb-3">Respuestas de los artesanos (últimos 14 días)</h3>
          <ul className="flex flex-col gap-2 text-sm">
            {summary.recent_answers.map((a, i) => (
              <li key={i} className="flex flex-wrap items-baseline justify-between gap-2">
                <Link to={a.link} className="text-botanica-negro hover:text-botanica-jade">
                  <span aria-hidden="true">{a.positive ? '✓ ' : '↺ '}</span>{a.text}
                </Link>
                <span className="text-xs text-botanica-gris">{formatDateTime(a.at)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {cards.length === 0 ? (
        <p className="rounded-xl border border-botanica-jade/25 bg-botanica-jade/5 p-4 text-sm text-botanica-negro">Todo al día ✓</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {cards.map((c) => {
            const bucket = summary[c.key] as SummaryBucket;
            return (
              <div key={c.key} className="bg-white border border-botanica-gris/15 rounded-xl p-5 shadow-sm flex flex-col gap-2">
                <div className="flex items-baseline justify-between gap-2">
                  <h3 className="font-medium text-botanica-negro">{c.title}</h3>
                  <span className="text-2xl font-serif tabular-nums text-botanica-negro">{bucket.count}</span>
                </div>
                {c.hint && <p className="text-xs text-botanica-gris">{c.hint}</p>}
                <ul className="flex flex-col gap-1 text-sm">
                  {bucket.items.map((item) => (
                    <li key={item.id}>
                      <Link to={c.to(item.id)} className="text-botanica-grafito underline decoration-botanica-gris/40 hover:text-botanica-jade">
                        {item.name}
                      </Link>
                      {item.detail && <span className="text-xs text-botanica-gris"> · {item.detail}</span>}
                    </li>
                  ))}
                  {bucket.count > bucket.items.length && (
                    <li className="text-xs text-botanica-gris">y {bucket.count - bucket.items.length} más</li>
                  )}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
