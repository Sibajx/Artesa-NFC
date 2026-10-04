import { useState } from 'react';
import { ApiError, adminApi } from './api';
import type { PieceDetail, Sale, SaleInput } from './api';
import { formatDateTime, writeErrorMessage } from './format';
import { useConfirm, useToast } from './feedback-context';

// P-026 G1: register the sale of a piece (the only way it becomes "Vendida")
// or cancel it. The buyer's name and contact are optional personal data.

const CHANNELS: { value: Sale['channel']; label: string }[] = [
  { value: 'taller', label: 'En el taller' },
  { value: 'tienda', label: 'Tienda' },
  { value: 'en_linea', label: 'En línea' },
  { value: 'feria', label: 'Feria o exposición' },
  { value: 'otro', label: 'Otro' },
];
const channelLabel = (c: string) => CHANNELS.find((x) => x.value === c)?.label ?? c;

const CONFLICTS: Record<string, string> = {
  already_sold: 'La pieza ya tiene una venta registrada. Cancélala primero.',
  not_sold: 'La pieza no tiene una venta registrada.',
  invalid_sale: 'Revisa los datos: la fecha no puede ser futura y el motivo necesita al menos 5 letras.',
};

const money = (cents: number, currency = 'MXN') =>
  (cents / 100).toLocaleString('es-MX', { style: 'currency', currency });

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && CONFLICTS[e.code]) return CONFLICTS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

function today(): string {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function parseCents(raw: string): number | null {
  const cleaned = raw.replace(/[$\s]/g, '').replace(/,(?=\d{3}(\D|$))/g, '').replace(',', '.');
  const n = Number(cleaned);
  return cleaned && Number.isFinite(n) && n >= 0 ? Math.round(n * 100) : null;
}

export function VentaSection({ piece, onChanged }: { piece: PieceDetail; onChanged: () => void }) {
  const sales = piece.sales ?? [];
  const active = sales.find((s) => s.status === 'active');
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = useConfirm();
  const toast = useToast();

  async function cancel() {
    const reason = await confirm({
      title: '¿Cancelar la venta?',
      body: 'La pieza vuelve a "Disponible". La venta queda en el historial y en la auditoría.',
      confirmLabel: 'Cancelar venta',
      tone: 'danger',
      reason: { label: 'Motivo', placeholder: 'Ej. el comprador se arrepintió; se registró por error' },
    });
    if (reason === null) return;
    setError(null);
    try {
      await adminApi.cancelSale(piece.id, reason.trim(), piece.updated_at);
      toast('Venta cancelada');
      onChanged();
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <section aria-labelledby="sale-heading" className="card-elevated p-5 sm:p-6 flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="sale-heading" className="text-xl font-serif text-botanica-negro">Venta</h2>
        {piece.price_cents !== null && piece.price_cents !== undefined && (
          <span className="text-sm text-botanica-grafito">Precio de lista: <strong>{money(piece.price_cents, piece.price_currency)}</strong></span>
        )}
      </div>
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      {active ? (
        <div className="flex flex-col gap-3 text-sm">
          <dl className="grid gap-3 sm:grid-cols-3">
            <div><dt className="text-botanica-gris">Fecha</dt><dd>{active.sold_on}</dd></div>
            <div><dt className="text-botanica-gris">Precio</dt><dd className="font-medium">{money(active.price_cents, active.currency)}</dd></div>
            <div><dt className="text-botanica-gris">Canal</dt><dd>{channelLabel(active.channel)}</dd></div>
            <div><dt className="text-botanica-gris">Vendió</dt><dd>{active.sold_by}</dd></div>
            {active.buyer_name && <div><dt className="text-botanica-gris">Comprador</dt><dd>{active.buyer_name}</dd></div>}
            {active.buyer_contact && <div><dt className="text-botanica-gris">Contacto</dt><dd className="break-all">{active.buyer_contact}</dd></div>}
            {active.note && <div className="sm:col-span-3"><dt className="text-botanica-gris">Nota</dt><dd>{active.note}</dd></div>}
          </dl>
          <p className="text-xs text-botanica-gris">Registró {active.recorded_by} · {formatDateTime(active.created_at)}</p>
          <div><button type="button" className="btn-secondary" onClick={() => void cancel()}>Cancelar venta</button></div>
        </div>
      ) : open ? (
        <SaleForm piece={piece} onDone={() => { setOpen(false); onChanged(); }} onCancel={() => setOpen(false)} setError={setError} />
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-botanica-grafito">Al registrar la venta, la pieza pasa a <strong>Vendida</strong> (también en el sitio).</p>
          <div><button type="button" className="btn-primary" onClick={() => setOpen(true)}>Registrar venta</button></div>
        </div>
      )}

      {sales.some((s) => s.status === 'cancelled') && (
        <details className="text-sm">
          <summary className="cursor-pointer text-botanica-grafito">Ventas canceladas</summary>
          <ul className="mt-2 flex flex-col gap-2">
            {sales.filter((s) => s.status === 'cancelled').map((s) => (
              <li key={s.id} className="rounded-lg border border-botanica-gris/20 p-3">
                {s.sold_on} · {money(s.price_cents, s.currency)} · {channelLabel(s.channel)} — cancelada: {s.cancel_reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function SaleForm({ piece, onDone, onCancel, setError }: {
  piece: PieceDetail; onDone: () => void; onCancel: () => void; setError: (m: string | null) => void;
}) {
  const [soldOn, setSoldOn] = useState(today());
  const [price, setPrice] = useState(piece.price_cents ? (piece.price_cents / 100).toFixed(2) : '');
  const [channel, setChannel] = useState<Sale['channel']>('taller');
  const [soldBy, setSoldBy] = useState(piece.artisan.full_name);
  const [buyerName, setBuyerName] = useState('');
  const [buyerContact, setBuyerContact] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const input = 'w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white focus:outline-none focus:border-botanica-jade';

  async function save() {
    const cents = parseCents(price);
    if (cents === null) {
      setError('Escribe el precio de venta, por ejemplo 3500 o 3,500.00.');
      return;
    }
    const body: SaleInput = {
      sold_on: soldOn, price_cents: cents, currency: piece.price_currency ?? 'MXN', channel, sold_by: soldBy.trim(),
      buyer_name: buyerName.trim() || null, buyer_contact: buyerContact.trim() || null, note: note.trim() || null,
    };
    setBusy(true);
    setError(null);
    try {
      await adminApi.registerSale(piece.id, body, piece.updated_at);
      toast('Venta registrada');
      onDone();
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  }

  return (
    <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <label className="text-xs font-medium text-botanica-grafito">Fecha de venta
        <input type="date" required max={today()} value={soldOn} onChange={(e) => setSoldOn(e.target.value)} className={input} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Precio de venta (MXN)
        <input required inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} className={input} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Canal
        <select value={channel} onChange={(e) => setChannel(e.target.value as Sale['channel'])} className={input}>
          {CHANNELS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Quién vendió
        <input required maxLength={120} value={soldBy} onChange={(e) => setSoldBy(e.target.value)} className={input} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Comprador (opcional)
        <input maxLength={120} value={buyerName} onChange={(e) => setBuyerName(e.target.value)} className={input} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Contacto del comprador (opcional)
        <input maxLength={120} value={buyerContact} onChange={(e) => setBuyerContact(e.target.value)} className={input} placeholder="Teléfono o correo" />
      </label>
      <label className="text-xs font-medium text-botanica-grafito sm:col-span-2">Nota (opcional)
        <textarea rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} className={input} />
      </label>
      <p className="text-xs text-botanica-gris sm:col-span-2">
        Antes de entregar: tarjeta impresa y probada, chip grabado y certificado original publicado (Certificación).
      </p>
      <div className="flex gap-2 sm:col-span-2">
        <button type="submit" className="btn-primary" disabled={busy}>Registrar venta</button>
        <button type="button" className="btn-secondary" disabled={busy} onClick={onCancel}>Cancelar</button>
      </div>
    </form>
  );
}
