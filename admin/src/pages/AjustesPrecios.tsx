import { useState } from 'react';
import { ApiError, adminApi } from '../api';
import type { PricingConfig, PricingSettings } from '../api';
import { formatDateTime, writeErrorMessage } from '../format';
import { useLoad } from '../hooks';
import { ErrorState, Loading } from '../ui';
import { useToast } from '../feedback-context';
import { Desglose } from '../PrecioSection';
import { money, parseCents, useQuote } from '../pricing';

// Ajustes de la calculadora (solo el dueño): modelo fiscal, tasas, tramos de
// comisión, cuota de venta directa y redondeo. Los valores por omisión son
// aproximados; se cambian aquí cuando el contador defina el régimen.

export default function AjustesPrecios() {
  const [revision, setRevision] = useState(0);
  const state = useLoad(`pricing:${revision}`, (s) => adminApi.pricingSettings(s));
  if (state.status === 'loading') return <Loading label="Cargando ajustes de precios…" />;
  if (state.status === 'error') return <ErrorState error={state.error} />;
  return <Form key={state.data.updated_at ?? 'defaults'} settings={state.data} onSaved={() => setRevision((r) => r + 1)} />;
}

type Draft = Record<'card_pct' | 'iva_pct' | 'isr_pct' | 'ret_isr_pct' | 'ret_iva_pct' | 'card_fixed' | 'direct_fee' | 'round_to', string>;

const num = (s: string) => String(Number(s));
const pesos = (cents: number) => String(cents / 100);

function Form({ settings, onSaved }: { settings: PricingSettings; onSaved: () => void }) {
  const c = settings.config;
  const [mode, setMode] = useState<PricingConfig['mode']>(c.mode);
  const [tiers, setTiers] = useState(c.tiers.map((t) => ({ up_to: t.up_to_cents === null ? '' : pesos(t.up_to_cents), pct: num(t.pct) })));
  const [d, setD] = useState<Draft>({
    card_pct: num(c.card_pct), iva_pct: num(c.iva_pct), isr_pct: num(c.isr_pct), ret_isr_pct: num(c.ret_isr_pct),
    ret_iva_pct: num(c.ret_iva_pct), card_fixed: pesos(c.card_fixed_cents), direct_fee: pesos(c.direct_fee_cents),
    round_to: pesos(c.round_to_cents),
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const { quote } = useQuote(600000, '20.00');
  const set = (k: keyof Draft) => (e: React.ChangeEvent<HTMLInputElement>) => setD({ ...d, [k]: e.target.value.replace(/[^\d.,]/g, '').slice(0, 12) });

  function build(): PricingConfig | null {
    const pct = (s: string) => { const n = Number(s.replace(',', '.')); return s !== '' && Number.isFinite(n) ? n.toFixed(2) : null; };
    const cents = (s: string) => parseCents(s);
    const out = {
      mode,
      tiers: tiers.map((t, i) => ({ up_to_cents: i === tiers.length - 1 ? null : cents(t.up_to), pct: pct(t.pct) })),
      card_pct: pct(d.card_pct), iva_pct: pct(d.iva_pct), isr_pct: pct(d.isr_pct), ret_isr_pct: pct(d.ret_isr_pct),
      ret_iva_pct: pct(d.ret_iva_pct), card_fixed_cents: cents(d.card_fixed), direct_fee_cents: cents(d.direct_fee),
      round_to_cents: cents(d.round_to),
    };
    const missing = [out.card_pct, out.iva_pct, out.isr_pct, out.ret_isr_pct, out.ret_iva_pct, out.card_fixed_cents,
      out.direct_fee_cents, out.round_to_cents, ...out.tiers.map((t) => t.pct),
      ...out.tiers.slice(0, -1).map((t) => t.up_to_cents)].some((v) => v === null);
    return missing ? null : (out as PricingConfig);
  }

  async function save() {
    const config = build();
    if (!config) {
      setError('Llena todos los campos con números.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await adminApi.savePricingSettings(config);
      toast('Ajustes de precios guardados');
      onSaved();
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError('network', 0);
      setError(err.status === 422
        ? 'Revisa los valores: porcentajes de 0 a 100, tramos de menor a mayor y redondeo mayor a 0.'
        : writeErrorMessage(err));
    }
    setBusy(false);
  }

  const input = 'w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white focus:outline-none focus:border-botanica-jade tabular-nums';
  const field = (label: string, k: keyof Draft, hint?: string) => (
    <label className="text-xs font-medium text-botanica-grafito">{label}
      <input inputMode="decimal" value={d[k]} onChange={set(k)} className={input} />
      {hint && <span className="mt-1 block font-normal text-botanica-gris">{hint}</span>}
    </label>
  );

  return (
    <div className="flex flex-col gap-6 max-w-5xl">
      <header className="flex flex-col gap-1">
        <h1 className="text-3xl font-serif text-botanica-negro">Ajustes de precios</h1>
        <p className="text-sm text-botanica-grafito">
          Las tasas de la calculadora de cada pieza. Son estimados: cámbialos cuando tu contador defina el régimen.
          {settings.updated_at && <> Último cambio: {settings.updated_by}, {formatDateTime(settings.updated_at)}.</>}
        </p>
      </header>
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <form className="flex flex-col gap-6" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <fieldset className="card-elevated p-5 flex flex-col gap-3">
            <legend className="text-lg font-serif text-botanica-negro px-1">Cómo factura Artesa</legend>
            {([['intermediario', 'Intermediario', 'Cobra solo su comisión; el IVA va sobre la comisión. Precios más bajos.'],
              ['revendedor', 'Revendedor', 'Compra la pieza y la revende; el IVA va sobre todo el precio.']] as const).map(([v, t, h]) => (
              <label key={v} className="flex gap-3 items-start text-sm cursor-pointer">
                <input type="radio" name="mode" value={v} checked={mode === v} onChange={() => setMode(v)} className="mt-1" />
                <span><strong className="text-botanica-negro">{t}</strong><span className="block text-botanica-grafito">{h}</span></span>
              </label>
            ))}
          </fieldset>

          <fieldset className="card-elevated p-5 flex flex-col gap-3">
            <legend className="text-lg font-serif text-botanica-negro px-1">Comisión según el precio del artesano</legend>
            <p className="text-xs text-botanica-gris">Se usa cuando la pieza no tiene una comisión propia.</p>
            {tiers.map((t, i) => {
              const last = i === tiers.length - 1;
              return (
                <div key={i} className="grid grid-cols-[1fr_7rem_auto] gap-2 items-end">
                  {last ? (
                    <p className="text-sm text-botanica-grafito pb-2">{tiers.length > 1 ? 'Más de lo anterior' : 'Cualquier precio'}</p>
                  ) : (
                    <label className="text-xs font-medium text-botanica-grafito">Hasta (MXN)
                      <input inputMode="decimal" value={t.up_to} className={input}
                        onChange={(e) => setTiers(tiers.map((x, j) => (j === i ? { ...x, up_to: e.target.value.replace(/[^\d.,]/g, '') } : x)))} />
                    </label>
                  )}
                  <label className="text-xs font-medium text-botanica-grafito">Comisión %
                    <input inputMode="decimal" value={t.pct} className={input}
                      onChange={(e) => setTiers(tiers.map((x, j) => (j === i ? { ...x, pct: e.target.value.replace(/[^\d.,]/g, '') } : x)))} />
                  </label>
                  <button type="button" className="btn-secondary !px-3" disabled={tiers.length === 1}
                    aria-label={`Quitar tramo ${i + 1}`} onClick={() => setTiers(tiers.filter((_, j) => j !== i))}>Quitar</button>
                </div>
              );
            })}
            <div>
              <button type="button" className="btn-secondary" disabled={tiers.length >= 10}
                onClick={() => setTiers([...tiers.slice(0, -1), { up_to: '', pct: tiers[tiers.length - 1].pct }, tiers[tiers.length - 1]])}>
                Agregar tramo
              </button>
            </div>
          </fieldset>

          <fieldset className="card-elevated p-5 grid gap-3 sm:grid-cols-2">
            <legend className="text-lg font-serif text-botanica-negro px-1">Cobro e impuestos</legend>
            {field('Cobro con tarjeta (%)', 'card_pct', 'Lo que cobra Mercado Pago, Stripe o Clip')}
            {field('Cuota fija por cobro (MXN)', 'card_fixed', 'Si tu procesador cobra un monto fijo')}
            {field('IVA (%)', 'iva_pct')}
            {field('ISR (%)', 'isr_pct', 'Persona moral: 30. RESICO persona física: 1 a 2.5')}
            {field('Retención de ISR al artesano (%)', 'ret_isr_pct', 'Solo si el SAT te trata como plataforma digital')}
            {field('Retención de IVA al artesano (%)', 'ret_iva_pct', 'Igual que la anterior; si no aplica, 0')}
          </fieldset>

          <fieldset className="card-elevated p-5 grid gap-3 sm:grid-cols-2">
            <legend className="text-lg font-serif text-botanica-negro px-1">Venta directa y redondeo</legend>
            {field('Cuota por venta directa del artesano (MXN)', 'direct_fee', 'Cubre chip e insumos; va encima de su precio')}
            {field('Redondear hacia arriba a múltiplos de (MXN)', 'round_to', '100 = a la centena de arriba')}
          </fieldset>

          <div><button type="submit" className="btn-primary" disabled={busy}>Guardar ajustes</button></div>
        </form>

        <aside className="card-elevated p-5 flex flex-col gap-3 h-fit lg:sticky lg:top-6" aria-label="Ejemplo con los ajustes guardados">
          <h2 className="text-lg font-serif text-botanica-negro">Ejemplo: pieza de {money(600000)} al 20 %</h2>
          <p className="text-xs text-botanica-gris">Con los ajustes guardados. Se actualiza al guardar.</p>
          {quote ? <Desglose q={quote} /> : <p className="text-sm text-botanica-gris">Calculando…</p>}
        </aside>
      </div>
    </div>
  );
}
