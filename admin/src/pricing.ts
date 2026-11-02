import { useEffect, useState } from 'react';
import { adminApi } from './api';
import type { PriceQuote } from './api';

// Shared by the piece's «Precio al público» and the «Ajustes de precios» page.

export const money = (cents: number) =>
  (cents / 100).toLocaleString('es-MX', { style: 'currency', currency: 'MXN', maximumFractionDigits: cents % 100 ? 2 : 0 });

export function parseCents(raw: string): number | null {
  const cleaned = raw.replace(/[$\s]/g, '').replace(/,(?=\d{3}(\D|$))/g, '').replace(',', '.');
  const n = Number(cleaned);
  return cleaned && Number.isFinite(n) && n >= 0 ? Math.round(n * 100) : null;
}

export const pctText = (pct: string) => `${Number(pct).toLocaleString('es-MX', { maximumFractionDigits: 2 })} %`;

export function useQuote(artisanCents: number | null, commission: string | null) {
  const [quote, setQuote] = useState<PriceQuote | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (artisanCents === null) return;
    let alive = true;
    const t = window.setTimeout(() => {
      adminApi.priceQuote(artisanCents, commission).then(
        (q) => { if (alive) { setQuote(q); setFailed(false); } },
        () => { if (alive) setFailed(true); },
      );
    }, 250);
    return () => { alive = false; window.clearTimeout(t); };
  }, [artisanCents, commission]);
  return { quote: artisanCents === null ? null : quote, failed };
}

