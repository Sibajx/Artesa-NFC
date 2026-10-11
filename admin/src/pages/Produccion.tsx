import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { adminApi } from '../api';
import type { ProductionBucket, ProductionCard } from '../api';
import { formatDateTime } from '../format';
import { useLoad } from '../hooks';
import { ProductionTimeline } from '../ProductionTimeline';
import { ErrorState, Loading, PageHeader } from '../ui';

// Hariel's board: every piece on its way from the artisan to the buyer, in tabs
// by stage. Open one to see its timeline and mark steps (photos optional).

const TABS: { key: ProductionBucket; label: string; hint: string }[] = [
  { key: 'pending', label: 'Pendientes', hint: 'Todavía no se recibe del artesano' },
  { key: 'in_process', label: 'En proceso', hint: 'Recibida; falta el chip o el embalaje' },
  { key: 'ready_to_ship', label: 'Por enviar', hint: 'Embalada, lista para salir' },
  { key: 'in_transit', label: 'En camino', hint: 'Enviada, aún sin entregar' },
  { key: 'delivered', label: 'Entregadas', hint: 'Ya la tiene el comprador' },
];

const STEP_LABEL: Record<string, string> = {
  received: 'Recibida', chip_placed: 'Chip colocado', chip_programmed: 'Chip programado', packed: 'Embalada', shipped: 'Enviada', delivered: 'Entregada',
};

export default function Produccion() {
  const [revision, setRevision] = useState(0);
  const state = useLoad(`production-board:${revision}`, (signal) => adminApi.productionBoard(signal));
  const [tab, setTab] = useState<ProductionBucket>('in_process');
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState<string | null>(null);

  const cards = useMemo(() => {
    const all = state.status === 'ready' ? state.data.data : [];
    const needle = q.trim().toLowerCase();
    return all.filter((c: ProductionCard) => c.bucket === tab && (!needle || `${c.name} ${c.public_code} ${c.artisan_name}`.toLowerCase().includes(needle)));
  }, [state, tab, q]);

  if (state.status === 'loading') return <Loading label="Cargando producción..." />;
  if (state.status === 'error') return <div className="card-elevated"><ErrorState error={state.error} /></div>;
  const { counts } = state.data;
  const chosen = state.data.data.find((c) => c.piece_id === selected) ?? null;

  return (
    <div className="max-w-6xl mx-auto pb-12 flex flex-col gap-6">
      <PageHeader title="Producción" subtitle="Dónde va cada pieza: del artesano al chip, al embalaje y al comprador." />

      <div role="tablist" aria-label="Etapas" className="flex gap-2 overflow-x-auto pb-1">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} title={t.hint}
            onClick={() => { setTab(t.key); setSelected(null); }}
            className={`shrink-0 rounded-full border px-4 py-2 text-sm font-medium ${tab === t.key
              ? 'border-botanica-jade bg-botanica-jade text-[var(--on-accent)]'
              : 'border-botanica-gris/30 bg-white text-botanica-grafito hover:border-botanica-jade'}`}>
            {t.label} <span className="ml-1 tabular-nums opacity-80">{counts[t.key]}</span>
          </button>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <section className="card-elevated overflow-hidden" aria-label="Piezas de la etapa">
          <div className="flex flex-wrap items-center gap-3 p-4 border-b border-botanica-gris/15">
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar pieza, código o artesano"
              aria-label="Buscar pieza, código o artesano"
              className="min-w-[12rem] flex-1 text-sm border border-botanica-gris/30 rounded-md px-3 py-1.5 bg-white text-botanica-negro" />
            <span className="text-xs text-botanica-gris" role="status">{cards.length} {cards.length === 1 ? 'pieza' : 'piezas'}</span>
          </div>
          {cards.length === 0 ? (
            <p className="p-10 text-center text-botanica-gris">{TABS.find((t) => t.key === tab)?.hint}. Aquí no hay piezas ahora.</p>
          ) : (
            <ul className="divide-y divide-botanica-gris/10" role="list">
              {cards.map((c) => (
                <li key={c.piece_id}>
                  <button type="button" onClick={() => setSelected(c.piece_id)} aria-pressed={selected === c.piece_id}
                    className={`flex w-full min-h-[44px] flex-col gap-1 px-5 py-4 text-left hover:bg-botanica-hueso ${selected === c.piece_id ? 'bg-botanica-hueso' : ''}`}>
                    <span className="flex items-center justify-between gap-3">
                      <span className="font-medium text-botanica-negro">{c.name}</span>
                      <span className="text-xs tabular-nums text-botanica-gris">{c.done}/{c.total}</span>
                    </span>
                    <span className="text-xs text-botanica-gris">{c.public_code} · {c.artisan_name}</span>
                    <span className="h-1.5 w-full overflow-hidden rounded-full bg-botanica-gris/20" aria-hidden="true">
                      <span className="block h-full rounded-full bg-botanica-jade" style={{ width: `${(c.done / c.total) * 100}%` }} />
                    </span>
                    {c.last_step && <span className="text-xs text-botanica-grafito">Último: {STEP_LABEL[c.last_step]}{c.last_done_at && <> · {formatDateTime(c.last_done_at)}</>}</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card-elevated p-5 sm:p-6" aria-label="Seguimiento de la pieza">
          {chosen ? (
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-2xl font-serif text-botanica-negro">{chosen.name}</h2>
                <Link to={`/piezas/${chosen.piece_id}`} className="text-sm text-botanica-jade underline">Abrir la pieza</Link>
              </div>
              <ProductionTimeline key={chosen.piece_id} pieceId={chosen.piece_id} onChanged={() => setRevision((r) => r + 1)} />
            </div>
          ) : (
            <p className="p-8 text-center text-botanica-gris">Elige una pieza de la lista para ver sus pasos y marcar el siguiente.</p>
          )}
        </section>
      </div>
    </div>
  );
}
