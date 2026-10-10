import { useState } from 'react';
import { ApiError, adminApi } from '../api';
import type { Supply, SupplyDetail, SupplyMovement } from '../api';
import { formatDateTime, writeErrorMessage } from '../format';
import { useToast } from '../feedback-context';
import { useLoad } from '../hooks';
import { Badge, ErrorState, Gate, Loading, PageHeader } from '../ui';

// Stock of supplies (chips, seals, scratch cards, epoxy...) as a ledger: the
// stock is the sum of purchases, uses, losses and adjustments. Reading is for
// anyone who can see; recording needs the Logistics permission (Gate).

const KINDS: { value: SupplyMovement['kind'] | 'adjust_up' | 'adjust_down'; label: string; hint: string }[] = [
  { value: 'purchase', label: 'Compra', hint: 'Entra al stock' },
  { value: 'use', label: 'Uso', hint: 'Se gastó en una pieza o trabajo' },
  { value: 'loss', label: 'Merma', hint: 'Se dañó o se perdió (pide motivo)' },
  { value: 'adjust_up', label: 'Ajuste: sumar', hint: 'El conteo físico es mayor (pide motivo)' },
  { value: 'adjust_down', label: 'Ajuste: restar', hint: 'El conteo físico es menor (pide motivo)' },
];
const KIND_LABEL: Record<string, string> = { purchase: 'Compra', use: 'Uso', loss: 'Merma', adjustment: 'Ajuste' };

const CONFLICTS: Record<string, string> = {
  insufficient_stock: 'No alcanza el stock para ese movimiento.',
  note_required: 'Explica el motivo (merma o ajuste).',
  duplicate: 'Ya hay un insumo con ese nombre.',
  inactive_supply: 'Este insumo está desactivado. Actívalo para registrar movimientos.',
  invalid_quantity: 'Escribe una cantidad mayor que cero.',
  invalid_supply: 'El nombre y la unidad son obligatorios.',
  invalid_movement: 'Revisa el movimiento (el costo solo va en compras y la pieza solo en usos).',
};

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && CONFLICTS[e.code]) return CONFLICTS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

const num = (v: string) => Number(v).toLocaleString('es-MX', { maximumFractionDigits: 3 });
const money = (cents: number) => (cents / 100).toLocaleString('es-MX', { style: 'currency', currency: 'MXN' });
const input = 'w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm text-botanica-negro bg-white focus:outline-none focus:border-botanica-jade';

function NewSupply({ onCreated }: { onCreated: (d: SupplyDetail) => void }) {
  const [name, setName] = useState('');
  const [unit, setUnit] = useState('pieza');
  const [min, setMin] = useState('0');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const d = await adminApi.createSupply({ name: name.trim(), unit: unit.trim(), min_stock: Number(min) || 0, note: null });
      toast('Insumo creado');
      setName('');
      setMin('0');
      onCreated(d);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="grid gap-3 md:grid-cols-[2fr_1fr_1fr_auto] items-end" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <label className="text-xs font-medium text-botanica-grafito">Insumo
        <input required value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="Ej. Chips NTAG213, Epoxi, Sellos" className={`mt-1 ${input}`} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Unidad
        <input required value={unit} maxLength={30} onChange={(e) => setUnit(e.target.value)} placeholder="pieza, ml, g, rollo" className={`mt-1 ${input}`} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Aviso al bajar de
        <input type="number" min="0" step="any" value={min} onChange={(e) => setMin(e.target.value)} className={`mt-1 ${input}`} />
      </label>
      <button type="submit" className="btn-primary" disabled={busy || !name.trim()}>Agregar</button>
      {error && <p role="alert" className="md:col-span-4 text-sm text-red-700">{error}</p>}
    </form>
  );
}

function Detail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [revision, setRevision] = useState(0);
  const loaded = useLoad(`supply:${id}:${revision}`, (signal) => adminApi.supply(id, signal));
  const pieces = useLoad('inventory-for-supplies', (signal) => adminApi.inventory(signal));
  const [kind, setKind] = useState<(typeof KINDS)[number]['value']>('purchase');
  const [quantity, setQuantity] = useState('');
  const [cost, setCost] = useState('');
  const [pieceId, setPieceId] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  if (loaded.status === 'loading') return <Loading label="Cargando insumo..." />;
  if (loaded.status === 'error') return <ErrorState error={loaded.error} />;
  const { supply: s, movements } = loaded.data;

  async function run(call: () => Promise<unknown>, done: string): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await call();
      toast(done);
      setRevision((r) => r + 1);
      onChanged();
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function record() {
    const real = kind === 'adjust_up' || kind === 'adjust_down' ? 'adjustment' : kind;
    const ok = await run(() => adminApi.recordSupplyMovement(id, {
      kind: real,
      quantity: Number(quantity),
      direction: kind === 'adjust_down' ? 'down' : 'up',
      unit_cost_cents: kind === 'purchase' && cost ? Math.round(Number(cost) * 100) : null,
      piece_id: kind === 'use' && pieceId ? pieceId : null,
      note: note.trim() || null,
    }), 'Movimiento registrado');
    if (ok) { setQuantity(''); setCost(''); setNote(''); setPieceId(''); }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-serif text-botanica-negro">{s.name}</h2>
          <p className="text-sm text-botanica-grafito">
            Hay <strong className="tabular-nums">{num(s.stock)}</strong> {s.unit}
            {Number(s.min_stock) > 0 && <> · aviso al bajar de {num(s.min_stock)}</>}
            {s.low && <span className="ml-2"><Badge tone="lavanda">Stock bajo</Badge></span>}
            {!s.active && <span className="ml-2"><Badge tone="neutral">Desactivado</Badge></span>}
          </p>
        </div>
        <Gate permission="logistics">
          <button type="button" className="btn-secondary" disabled={busy}
            onClick={() => void run(() => adminApi.updateSupply(id, { active: !s.active }), s.active ? 'Insumo desactivado' : 'Insumo activado')}>
            {s.active ? 'Desactivar' : 'Activar'}
          </button>
        </Gate>
      </div>

      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      <Gate permission="logistics">
        <form className="grid gap-3 md:grid-cols-4 items-end rounded-xl border border-botanica-gris/20 p-4"
          onSubmit={(e) => { e.preventDefault(); void record(); }}>
          <label className="text-xs font-medium text-botanica-grafito">Movimiento
            <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} className={`mt-1 ${input}`}>
              {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </label>
          <label className="text-xs font-medium text-botanica-grafito">Cantidad ({s.unit})
            <input required type="number" min="0" step="any" value={quantity} onChange={(e) => setQuantity(e.target.value)} className={`mt-1 ${input}`} />
          </label>
          {kind === 'purchase' && (
            <label className="text-xs font-medium text-botanica-grafito">Costo por unidad (MXN, opcional)
              <input type="number" min="0" step="any" value={cost} onChange={(e) => setCost(e.target.value)} className={`mt-1 ${input}`} />
            </label>
          )}
          {kind === 'use' && (
            <label className="text-xs font-medium text-botanica-grafito">Pieza (opcional)
              <select value={pieceId} onChange={(e) => setPieceId(e.target.value)} className={`mt-1 ${input}`}>
                <option value="">Sin pieza</option>
                {pieces.status === 'ready' && pieces.data.data.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.public_code}</option>)}
              </select>
            </label>
          )}
          <label className={`text-xs font-medium text-botanica-grafito ${kind === 'purchase' || kind === 'use' ? 'md:col-span-3' : 'md:col-span-2'}`}>
            Nota{kind === 'loss' || kind.startsWith('adjust') ? ' (obligatoria)' : ' (opcional)'}
            <input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="Ej. Pedido a Mercado Libre; se dañaron al programar"
              required={kind === 'loss' || kind.startsWith('adjust')} className={`mt-1 ${input}`} />
          </label>
          <button type="submit" className="btn-primary" disabled={busy || !quantity}>Registrar</button>
          <p className="md:col-span-4 text-xs text-botanica-gris">{KINDS.find((k) => k.value === kind)?.hint}</p>
        </form>
      </Gate>

      <div className="overflow-x-auto rounded-xl border border-botanica-gris/15">
        <table className="w-full text-left border-collapse text-sm">
          <caption className="sr-only">Movimientos de {s.name}</caption>
          <thead>
            <tr className="table-header text-botanica-gris">
              <th scope="col" className="py-2.5 px-4 font-medium">Fecha</th>
              <th scope="col" className="py-2.5 px-4 font-medium">Movimiento</th>
              <th scope="col" className="py-2.5 px-4 font-medium text-right">Cantidad</th>
              <th scope="col" className="py-2.5 px-4 font-medium">Detalle</th>
              <th scope="col" className="py-2.5 px-4 font-medium">Quién</th>
            </tr>
          </thead>
          <tbody className="text-botanica-grafito">
            {movements.length === 0 && <tr><td colSpan={5} className="p-6 text-center text-botanica-gris">Todavía no hay movimientos.</td></tr>}
            {movements.map((m) => (
              <tr key={m.id} className="border-t border-botanica-gris/10">
                <td className="py-2.5 px-4 whitespace-nowrap">{formatDateTime(m.created_at)}</td>
                <td className="py-2.5 px-4">{KIND_LABEL[m.kind] ?? m.kind}</td>
                <td className={`py-2.5 px-4 text-right tabular-nums ${Number(m.delta) < 0 ? 'text-red-700' : ''}`}>{Number(m.delta) > 0 ? '+' : ''}{num(m.delta)}</td>
                <td className="py-2.5 px-4">
                  {[m.piece_name && `Pieza: ${m.piece_name}`, m.unit_cost_cents !== null && `${money(m.unit_cost_cents)} c/u`, m.note].filter(Boolean).join(' · ') || '—'}
                </td>
                <td className="py-2.5 px-4 text-botanica-gris">{m.recorded_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function Insumos() {
  const [revision, setRevision] = useState(0);
  const [showInactive, setShowInactive] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const state = useLoad(`supplies:${revision}:${showInactive}`, (signal) => adminApi.supplies(showInactive, signal));

  if (state.status === 'loading') return <Loading label="Cargando insumos..." />;
  if (state.status === 'error') return <div className="card-elevated"><ErrorState error={state.error} /></div>;
  const { data, low_count } = state.data;
  const refresh = () => setRevision((r) => r + 1);

  return (
    <div className="max-w-5xl mx-auto pb-12 flex flex-col gap-6">
      <PageHeader title="Insumos" subtitle="Chips, sellos, rasca y gana, epoxi y todo lo que se gasta al certificar una pieza." />
      {low_count > 0 && (
        <p role="status" className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-botanica-negro">
          {low_count === 1 ? 'Hay 1 insumo con stock bajo.' : `Hay ${low_count} insumos con stock bajo.`}
        </p>
      )}

      <Gate permission="logistics">
        <section className="card-elevated p-5 sm:p-6 flex flex-col gap-3" aria-labelledby="new-supply">
          <h2 id="new-supply" className="text-xl font-serif text-botanica-negro">Nuevo insumo</h2>
          <NewSupply onCreated={(d) => { refresh(); setSelected(d.supply.id); }} />
        </section>
      </Gate>

      <section className="card-elevated overflow-hidden" aria-label="Insumos">
        <div className="flex items-center justify-between gap-3 p-4 border-b border-botanica-gris/15">
          <p className="text-sm text-botanica-grafito">{data.length} {data.length === 1 ? 'insumo' : 'insumos'}</p>
          <label className="flex items-center gap-2 text-xs text-botanica-grafito">
            <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} className="accent-botanica-jade" />
            Mostrar desactivados
          </label>
        </div>
        {data.length === 0 ? (
          <p className="p-12 text-center text-botanica-gris">Todavía no hay insumos. Agrega el primero arriba (por ejemplo, los chips NTAG).</p>
        ) : (
          <table className="w-full text-left border-collapse text-sm">
            <caption className="sr-only">Insumos y su stock</caption>
            <thead>
              <tr className="table-header text-botanica-gris">
                <th scope="col" className="py-3 px-5 font-medium">Insumo</th>
                <th scope="col" className="py-3 px-5 font-medium text-right">Stock</th>
                <th scope="col" className="py-3 px-5 font-medium text-right">Aviso en</th>
                <th scope="col" className="py-3 px-5 font-medium"><span className="sr-only">Estado</span></th>
              </tr>
            </thead>
            <tbody className="text-botanica-grafito">
              {data.map((s: Supply) => (
                <tr key={s.id} className={`border-b border-botanica-gris/10 last:border-0 table-row-hover ${selected === s.id ? 'bg-botanica-hueso' : ''}`}>
                  <td className="py-3 px-5">
                    <button type="button" aria-pressed={selected === s.id} onClick={() => setSelected(selected === s.id ? null : s.id)}
                      className="font-medium text-botanica-negro hover:text-botanica-jade text-left">{s.name}</button>
                    {s.note && <span className="block text-xs text-botanica-gris">{s.note}</span>}
                  </td>
                  <td className="py-3 px-5 text-right tabular-nums">{num(s.stock)} <span className="text-botanica-gris">{s.unit}</span></td>
                  <td className="py-3 px-5 text-right tabular-nums text-botanica-gris">{Number(s.min_stock) > 0 ? num(s.min_stock) : '—'}</td>
                  <td className="py-3 px-5 text-right">
                    {s.low && <Badge tone="lavanda">Stock bajo</Badge>}
                    {!s.active && <Badge tone="neutral">Desactivado</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {selected && (
        <section className="card-elevated p-5 sm:p-6" aria-label="Detalle del insumo">
          <Detail key={selected} id={selected} onChanged={refresh} />
        </section>
      )}
    </div>
  );
}
