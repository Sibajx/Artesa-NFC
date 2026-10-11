import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { adminApi, PIECES_CSV_URL } from '../api';
import type { InventoryRow } from '../api';
import { formatDate, labels } from '../format';
import { useLoad } from '../hooks';
import { Badge, ErrorState, Loading, PageHeader, PublicationBadge } from '../ui';

// Inventory of pieces: every piece outside the trash with where it is, what it
// costs and whether it is sold. Read-only; pieces are edited from their own page.
// (Pieces are unique works, ADR-011: this is not stock of units. Supplies are on /insumos.)

const LOCATIONS: Record<string, string> = {
  taller: 'Taller del artesano', bodega: 'Bodega', tienda: 'Tienda', exhibicion: 'Exhibición o feria',
  transito: 'En tránsito', entregada: 'Entregada al comprador', otro: 'Otro',
};

const money = (cents: number | null, currency = 'MXN') =>
  cents === null ? '—' : (cents / 100).toLocaleString('es-MX', { style: 'currency', currency });

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="card-elevated p-5">
      <p className="text-xs uppercase tracking-wide text-botanica-gris">{label}</p>
      <p className="mt-1 text-3xl font-serif tabular-nums text-botanica-negro">{value}</p>
      {hint && <p className="mt-1 text-xs text-botanica-gris">{hint}</p>}
    </div>
  );
}

export default function Inventario() {
  const state = useLoad('inventory', (signal) => adminApi.inventory(signal));
  const [q, setQ] = useState('');
  const [availability, setAvailability] = useState('');
  const [location, setLocation] = useState('');

  const rows = useMemo(() => (state.status === 'ready' ? state.data.data : []), [state]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows.filter((r: InventoryRow) =>
      (!availability || r.availability_status === availability) &&
      (!location || (location === 'none' ? !r.location : r.location === location)) &&
      (!needle || `${r.name} ${r.public_code} ${r.artisan_name}`.toLowerCase().includes(needle)));
  }, [rows, q, availability, location]);

  if (state.status === 'loading') return <Loading label="Cargando inventario..." />;
  if (state.status === 'error') return <div className="card-elevated"><ErrorState error={state.error} /></div>;
  const { summary } = state.data;
  const count = (k: string) => summary.by_availability[k] ?? 0;
  const select = 'text-sm border border-botanica-gris/30 rounded-md px-3 py-1.5 bg-white text-botanica-negro';

  return (
    <div className="max-w-6xl mx-auto pb-12 flex flex-col gap-6">
      <PageHeader title="Inventario" subtitle="Dónde está cada pieza y en qué estado. Cada pieza es única; los insumos están en Insumos.">
        <a href={PIECES_CSV_URL} download className="btn-secondary">Exportar CSV</a>
      </PageHeader>

      <section aria-label="Resumen" className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <Tile label="Piezas" value={summary.total} />
        <Tile label="Disponibles" value={count('available')} hint={`${money(summary.available_value_cents, summary.currency)} en precio`} />
        <Tile label="Reservadas" value={count('reserved')} />
        <Tile label="En exhibición" value={count('exhibited')} />
        <Tile label="Vendidas" value={count('sold')} />
      </section>

      <section className="card-elevated overflow-hidden" aria-label="Piezas">
        <div className="flex flex-wrap items-center gap-3 p-4 border-b border-botanica-gris/15">
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar pieza, código o artesano"
            aria-label="Buscar pieza, código o artesano" className={`${select} min-w-[16rem] flex-1`} />
          <select value={availability} onChange={(e) => setAvailability(e.target.value)} aria-label="Disponibilidad" className={select}>
            <option value="">Toda disponibilidad</option>
            {['available', 'reserved', 'exhibited', 'sold', 'archived'].map((a) => <option key={a} value={a}>{labels.availability(a)}</option>)}
          </select>
          <select value={location} onChange={(e) => setLocation(e.target.value)} aria-label="Ubicación" className={select}>
            <option value="">Toda ubicación</option>
            {Object.entries(LOCATIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            <option value="none">Sin ubicación registrada</option>
          </select>
          <span className="ml-auto text-xs text-botanica-gris" role="status">{shown.length} de {rows.length}</span>
        </div>
        {shown.length === 0 ? (
          <p className="p-12 text-center text-botanica-gris">{rows.length === 0 ? 'Todavía no hay piezas registradas.' : 'Ninguna pieza coincide con los filtros.'}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-sm">
              <caption className="sr-only">Inventario de piezas ({shown.length})</caption>
              <thead>
                <tr className="table-header text-botanica-gris">
                  <th scope="col" className="py-3 px-5 font-medium">Pieza</th>
                  <th scope="col" className="py-3 px-5 font-medium">Artesano</th>
                  <th scope="col" className="py-3 px-5 font-medium">Disponibilidad</th>
                  <th scope="col" className="py-3 px-5 font-medium">Ubicación</th>
                  <th scope="col" className="py-3 px-5 font-medium text-right">Precio</th>
                  <th scope="col" className="py-3 px-5 font-medium">Publicación</th>
                </tr>
              </thead>
              <tbody className="text-botanica-grafito">
                {shown.map((r) => (
                  <tr key={r.id} className="border-b border-botanica-gris/10 last:border-0 table-row-hover">
                    <td className="py-3 px-5">
                      <Link to={`/piezas/${r.id}`} className="font-medium text-botanica-negro hover:text-botanica-jade">{r.name}</Link>
                      <span className="block text-xs text-botanica-gris">{r.public_code}</span>
                    </td>
                    <td className="py-3 px-5"><Link to={`/artesanos/${r.artisan_id}`} className="hover:text-botanica-jade">{r.artisan_name}</Link></td>
                    <td className="py-3 px-5">
                      <Badge tone={r.availability_status === 'available' ? 'jade' : 'neutral'}>{labels.availability(r.availability_status)}</Badge>
                      {r.sold_on && <span className="block text-xs text-botanica-gris">el {formatDate(r.sold_on)}</span>}
                    </td>
                    <td className="py-3 px-5">
                      {r.location ? <>{LOCATIONS[r.location] ?? r.location}{r.place && <span className="block text-xs text-botanica-gris">{r.place}</span>}</> : <span className="text-botanica-gris">Sin registrar</span>}
                    </td>
                    <td className="py-3 px-5 text-right tabular-nums">{money(r.price_cents, r.price_currency)}</td>
                    <td className="py-3 px-5"><PublicationBadge status={r.publication_status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
