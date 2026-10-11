import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { adminApi } from '../api';
import type { PieceSummary, ProductionBoard, PublicationStatus, Summary, SummaryBucket } from '../api';
import { actionLabel, actorLabel } from '../audit-labels';
import { formatDateTime } from '../format';
import { greetingFor } from '../greeting';
import { seasonOf } from '../season';
import type { Season } from '../season';
import { useLoad } from '../hooks';
import { useEmail, usePermissions, useRoles } from '../roles-context';
import { ErrorState, Loading, PageHeader } from '../ui';

function countBy(items: { publication_status: PublicationStatus }[]) {
  const counts = { draft: 0, published: 0, archived: 0 };
  for (const item of items) counts[item.publication_status] += 1;
  return counts;
}

// Something that asks for attention and is not one of the API's summary cards:
// it comes from a page this account can already read.
interface Extra {
  title: string;
  count: number;
  hint: string;
  to: string;
}

export default function Dashboard() {
  const email = useEmail();
  const permissions = usePermissions();
  const roles = useRoles();
  const greeting = useMemo(() => greetingFor(email), [email]);
  const season = useMemo(() => seasonOf(), []);
  const state = useLoad('dashboard', async (signal) => {
    // The extra reads are optional: if one fails, Inicio just shows less.
    const reviewer = roles.includes('owner') && permissions.includes('training');
    const [artisans, pieces, events, summary, board, supplies, manuals] = await Promise.all([
      adminApi.artisans({}, signal),
      adminApi.pieces({}, signal),
      adminApi.auditEvents({ limit: '5' }, signal),
      adminApi.summary(signal),
      adminApi.productionBoard(signal).catch(() => null),
      adminApi.supplies(false, signal).catch(() => null),
      reviewer ? adminApi.manuals(signal).catch(() => null) : Promise.resolve(null),
    ]);
    const extras: Extra[] = [];
    const ready = board?.counts.ready_to_ship ?? 0;
    if (ready > 0) extras.push({ title: 'Piezas listas para enviar', count: ready, hint: 'Empacadas, esperando salir', to: '/produccion' });
    if (supplies && supplies.low_count > 0) extras.push({ title: 'Insumos con stock bajo', count: supplies.low_count, hint: 'Conviene reponerlos', to: '/insumos' });
    const toReview = manuals?.data.filter((m) => m.status === 'in_review').length ?? 0;
    if (toReview > 0) extras.push({ title: 'Manuales por revisar', count: toReview, hint: 'Esperan tu respuesta', to: '/capacitaciones' });
    return { artisans: artisans.data, pieces: pieces.data, events: events.data, summary, board, extras };
  });

  return (
    <div className="max-w-6xl mx-auto pb-12">
      {season && <SeasonBanner season={season} />}
      <PageHeader title={greeting} subtitle={state.status === 'ready' ? attentionLine(state.data.summary, state.data.extras) : 'Lo que hay y lo que falta en ArtesaNFC.'} />

      {state.status === 'loading' && <Loading label="Cargando inicio..." />}
      {state.status === 'error' && <div className="card-elevated"><ErrorState error={state.error} /></div>}
      {state.status === 'ready' && (() => {
        const artisans = countBy(state.data.artisans);
        const pieces = countBy(state.data.pieces);
        return (
          <>
            <QuickActions />

            <Pending summary={state.data.summary} extras={state.data.extras} />

            <PieceJourney pieces={state.data.pieces} board={state.data.board} />

            <p className="mb-12 flex flex-wrap gap-x-6 gap-y-1 text-sm text-botanica-grafito">
              <Link to="/artesanos" className="hover:text-botanica-jade">
                <strong>{state.data.artisans.length}</strong> artesanos ({artisans.published} publicados · {artisans.draft} borradores)
              </Link>
              <Link to="/piezas" className="hover:text-botanica-jade">
                <strong>{state.data.pieces.length}</strong> piezas ({pieces.published} publicadas · {pieces.draft} borradores)
              </Link>
              <Link to="/archivo" className="hover:text-botanica-jade">
                <strong>{artisans.archived + pieces.archived}</strong> archivados
              </Link>
            </p>

            <section>
              <h2 className="text-2xl font-serif text-botanica-negro mb-6">Actividad reciente</h2>
              <div className="bg-white border border-botanica-gris/15 rounded-xl overflow-hidden shadow-sm">
                {state.data.events.length === 0 ? (
                  <p className="p-12 text-center text-botanica-gris">Todavía no hay actividad. Aquí aparecerá lo último que haga el equipo.</p>
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
              <p className="mt-3 text-sm"><Link to="/auditoria" className="text-botanica-jade hover:underline">Ver toda la auditoría</Link></p>
            </section>
          </>
        );
      })()}
    </div>
  );
}

// A row of little paper flags (papel picado) over the greeting while a season
// lasts: marigold and earth tones for Día de Muertos, green and red for Navidad.
// Pure decoration (hidden from screen readers) with the colors of the theme, so it
// reads in light and dark; it only sways when the person allows motion.
const FLAG_COLORS: Record<Season, string[]> = {
  muertos: ['var(--marigold)', 'var(--accent)', 'var(--danger-solid)'],
  navidad: ['var(--ok)', 'var(--danger-solid)', 'var(--marigold)'],
};

function SeasonBanner({ season }: { season: Season }) {
  const colors = FLAG_COLORS[season];
  return (
    <svg viewBox="0 0 420 40" role="presentation" aria-hidden="true" className="season-banner mb-4 w-full max-w-md h-auto">
      <line x1="0" y1="4" x2="420" y2="4" stroke="var(--rule)" strokeWidth="1" />
      {Array.from({ length: 14 }, (_, i) => {
        const x = 6 + i * 29;
        return (
          <path
            key={i}
            className="season-flag"
            style={{ animationDelay: `${(i % 5) * 160}ms` }}
            fill={colors[i % colors.length]}
            fillRule="evenodd"
            opacity="0.9"
            d={`M${x} 4H${x + 24}V28L${x + 20} 34L${x + 16} 28L${x + 12} 34L${x + 8} 28L${x + 4} 34L${x} 28ZM${x + 12} 10l4 5l-4 5l-4 -5z`}
          />
        );
      })}
    </svg>
  );
}

// The few things people do most, only the ones this account is allowed to do.
const SHORTCUTS: { label: string; to: string; permission: string }[] = [
  { label: 'Nueva pieza', to: '/piezas/nueva', permission: 'edit' },
  { label: 'Registrar visita', to: '/visitas', permission: 'visits' },
  { label: 'Certificar', to: '/certificacion', permission: 'nfc' },
  { label: 'Producción', to: '/produccion', permission: 'production' },
  { label: 'Nuevo manual', to: '/capacitaciones', permission: 'training' },
];

function QuickActions() {
  const permissions = usePermissions();
  const shown = SHORTCUTS.filter((s) => permissions.includes(s.permission)).slice(0, 4);
  if (shown.length === 0) return null;
  return (
    <nav aria-label="Accesos rápidos" className="mb-10 flex flex-wrap gap-3">
      {shown.map((s) => (
        <Link key={s.to} to={s.to} className="btn-secondary">{s.label}</Link>
      ))}
    </nav>
  );
}

// Where the pieces are on their way, from draft to delivered. The last two steps
// come from the production board, so they show only to accounts that can read it.
function PieceJourney({ pieces, board }: { pieces: PieceSummary[]; board: ProductionBoard | null }) {
  const live = pieces.filter((p) => p.publication_status !== 'archived');
  const sold = live.filter((p) => p.availability_status === 'sold').length;
  const steps: { label: string; count: number; to: string }[] = [
    { label: 'Borrador', count: live.filter((p) => p.publication_status === 'draft' && p.availability_status !== 'sold').length, to: '/piezas' },
    { label: 'Publicadas', count: live.filter((p) => p.publication_status === 'published' && p.availability_status !== 'sold').length, to: '/piezas' },
    { label: 'Vendidas', count: sold, to: '/piezas?vista=inventario' },
  ];
  if (board) {
    steps.push({ label: 'En camino', count: board.counts.in_transit, to: '/produccion' });
    steps.push({ label: 'Entregadas', count: board.counts.delivered, to: '/produccion' });
  }
  return (
    <section aria-labelledby="journey-heading" className="mb-12">
      <h2 id="journey-heading" className="text-2xl font-serif text-botanica-negro mb-4">El camino de las piezas</h2>
      <ol className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {steps.map((step, i) => (
          <li key={step.label}>
            <Link to={step.to} className="bg-white border border-botanica-gris/15 rounded-xl p-4 shadow-sm flex flex-col hover:border-botanica-jade/40">
              <span className="text-xs text-botanica-gris">{i + 1}. {step.label}</span>
              <span className="text-3xl font-serif tabular-nums text-botanica-negro">{step.count}</span>
            </Link>
          </li>
        ))}
      </ol>
    </section>
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

// One sentence under the greeting with what asks for attention first, from the
// same cards listed in Pendientes (the API only returns the ones for this account).
function attentionLine(summary: Summary, extras: Extra[]): string {
  const open = [
    ...CARDS.map((c) => ({ title: c.title, count: (summary[c.key] as SummaryBucket | undefined)?.count ?? 0 })),
    ...extras,
  ].filter((c) => c.count > 0);
  if (open.length === 0) return 'Todo al día ✓ Lo que hay y lo que falta en ArtesaNFC.';
  const total = open.reduce((sum, c) => sum + c.count, 0);
  const top = open.slice(0, 2).map((c) => `${c.title.toLowerCase()} (${c.count})`).join(' y ');
  return `${total === 1 ? 'Hay 1 cosa' : `Hay ${total} cosas`} por atender: ${top}${open.length > 2 ? ' y más' : ''}.`;
}

function Pending({ summary, extras }: { summary: Summary; extras: Extra[] }) {
  const cards = CARDS.filter((c) => {
    const bucket = summary[c.key] as SummaryBucket | undefined;
    return bucket && bucket.count > 0;
  });
  const sales = summary.sales_last_30_days;
  return (
    <section aria-labelledby="pending-heading" className="mb-12 flex flex-col gap-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="pending-heading" className="text-2xl font-serif text-botanica-negro">Para ti hoy</h2>
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

      {cards.length === 0 && extras.length === 0 ? (
        <p className="rounded-xl border border-botanica-jade/25 bg-botanica-jade/5 p-4 text-sm text-botanica-negro">Todo al día ✓ No hay nada esperándote. Buen momento para visitar a un artesano o repasar un manual.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {extras.map((e) => (
            <div key={e.title} className="bg-white border border-botanica-gris/15 rounded-xl p-5 shadow-sm flex flex-col gap-2">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="font-medium text-botanica-negro">{e.title}</h3>
                <span className="text-2xl font-serif tabular-nums text-botanica-negro">{e.count}</span>
              </div>
              <p className="text-xs text-botanica-gris">{e.hint}</p>
              <Link to={e.to} className="text-sm text-botanica-grafito underline decoration-botanica-gris/40 hover:text-botanica-jade">Ir allí</Link>
            </div>
          ))}
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
