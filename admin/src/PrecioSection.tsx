import { useState } from 'react';
import { ApiError, adminApi } from './api';
import type { PieceDetail, PriceQuote } from './api';
import { money, parseCents, pctText, useQuote } from './pricing';
import { writeErrorMessage } from './format';
import { useToast } from './feedback-context';
import { typedPrice } from './numbers';

// Calculadora de precio al público: the artisan gets their price in full;
// Artesa adds its commission, the card fee and IVA, and rounds up to the
// hundred. ISR is an estimate taken from the commission. The numbers come from
// the API (one formula for Gestión and, later, the shop).

export function Desglose({ q }: { q: PriceQuote }) {
  const row = (label: string, cents: number, hint?: string) => (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="text-botanica-grafito">{label}{hint && <span className="block text-xs text-botanica-gris">{hint}</span>}</dt>
      <dd className="tabular-nums text-right">{money(cents)}</dd>
    </div>
  );
  return (
    <div className="flex flex-col gap-4">
      <dl className="text-sm divide-y divide-botanica-gris/15">
        {row('Recibe el artesano', q.artisan_cents, 'Completo, sin regateo')}
        {q.retentions_cents > 0 && row('Retenciones de plataforma', q.retentions_cents, 'Se suman para que el artesano reciba su precio')}
        {row(`Comisión de Artesa (${pctText(q.commission_pct)})`, q.agreed_commission_cents)}
        {q.rounding_cents > 0 && row('Redondeo a tu favor', q.gross_commission_cents - q.agreed_commission_cents, 'Lo que suma subir a la centena, ya sin IVA ni cobro')}
        {row('IVA', q.iva_cents, q.mode === 'intermediario' ? 'Solo sobre la comisión (intermediario)' : 'Sobre todo el precio (revendedor)')}
        {row('Cobro con tarjeta', q.card_fee_cents)}
      </dl>
      <div className="rounded-xl bg-botanica-jade/10 p-4 flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-botanica-negro">Precio al público</span>
        <span className="text-3xl font-serif text-botanica-negro tabular-nums">{money(q.public_price_cents)}</span>
        <span className="w-full text-xs text-botanica-gris">Exacto {money(q.exact_price_cents)}, redondeado a la centena de arriba. El envío se cobra aparte.</span>
      </div>
      <dl className="text-sm divide-y divide-botanica-gris/15">
        {row('Comisión bruta', q.gross_commission_cents)}
        {row('ISR estimado', q.isr_cents, 'Antes de deducciones')}
        <div className="flex items-baseline justify-between gap-4 py-2">
          <dt className="font-medium text-botanica-negro">Te quedan aprox.</dt>
          <dd className="text-xl font-serif text-botanica-jade tabular-nums">{money(q.net_cents)}</dd>
        </div>
      </dl>
      <p className="text-xs text-botanica-gris">
        Si el artesano la vende él mismo: {money(q.direct_sale_cents)} (su precio más la cuota de Artesa).
        Cifras estimadas: confirma las tasas con tu contador.
      </p>
    </div>
  );
}

export function PrecioSection({ piece, onChanged }: { piece: PieceDetail; onChanged: () => void }) {
  const [artisan, setArtisan] = useState(piece.artisan_price_cents != null ? String(piece.artisan_price_cents / 100) : '');
  const [commission, setCommission] = useState(piece.commission_pct != null ? String(Number(piece.commission_pct)) : '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const artisanCents = parseCents(artisan);
  const pct = commission.trim() === '' ? null : Number(commission.replace(',', '.'));
  const pctValid = pct === null || (Number.isFinite(pct) && pct >= 0 && pct <= 100);
  const { quote, failed } = useQuote(artisanCents, pctValid && pct !== null ? pct.toFixed(2) : null);
  const auto = commission.trim() === '';

  async function save(useAsList: boolean) {
    if (artisanCents === null || !pctValid) {
      setError('Revisa el precio del artesano y la comisión (de 0 a 100).');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await adminApi.updatePiece(piece.id, piece.updated_at, {
        artisan_price_cents: artisanCents,
        commission_pct: pct === null ? null : pct.toFixed(2),
        ...(useAsList && quote ? { price_cents: quote.public_price_cents, price_currency: 'MXN' } : {}),
      });
      toast(useAsList ? 'Precio de lista actualizado' : 'Precio del artesano guardado');
      onChanged();
    } catch (e) {
      setError(writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0)));
    }
    setBusy(false);
  }

  const input = 'w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white focus:outline-none focus:border-botanica-jade';
  return (
    <section aria-labelledby="price-heading" className="card-elevated p-5 sm:p-6 flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="price-heading" className="text-xl font-serif text-botanica-negro">Precio al público</h2>
        {piece.price_cents != null && (
          <span className="text-sm text-botanica-grafito">Precio de lista actual: <strong>{money(piece.price_cents)}</strong></span>
        )}
      </div>
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs font-medium text-botanica-grafito">Lo que recibe el artesano (MXN)
          <input inputMode="decimal" value={artisan} onChange={(e) => setArtisan(typedPrice(e.target.value))} className={input}
            placeholder="Ej. 6000" />
        </label>
        <label className="text-xs font-medium text-botanica-grafito">Comisión de Artesa (%)
          <input inputMode="decimal" value={commission} onChange={(e) => setCommission(e.target.value.replace(/[^\d.,]/g, '').slice(0, 6))}
            className={input} placeholder={auto && quote ? `Automática: ${pctText(quote.commission_pct)}` : 'Automática según el monto'}
            aria-invalid={!pctValid} />
        </label>
      </div>
      {!pctValid && <p className="text-xs text-red-700">La comisión va de 0 a 100.</p>}
      {artisanCents === null ? (
        <p className="text-sm text-botanica-gris">Escribe lo que recibe el artesano para ver el precio sugerido.</p>
      ) : failed ? (
        <p role="alert" className="text-sm text-red-800">No se pudo calcular. Revisa tu conexión.</p>
      ) : quote ? (
        <Desglose q={quote} />
      ) : (
        <p className="text-sm text-botanica-gris">Calculando…</p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-primary" disabled={busy || !quote} onClick={() => void save(true)}>
          Usar {quote ? money(quote.public_price_cents) : ''} como precio de lista
        </button>
        <button type="button" className="btn-secondary" disabled={busy || artisanCents === null} onClick={() => void save(false)}>
          Solo guardar precio del artesano
        </button>
      </div>
    </section>
  );
}
