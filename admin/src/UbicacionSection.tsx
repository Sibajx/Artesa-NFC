import { useState } from 'react';
import { ApiError, adminApi } from './api';
import type { LocationKind, PieceDetail } from './api';
import { formatDateTime, writeErrorMessage } from './format';
import { useToast } from './feedback-context';

// P-026 G12: where the piece physically is, with its history of moves.
// Gestión only: the public site never shows it.

const LOCATIONS: { value: LocationKind; label: string }[] = [
  { value: 'taller', label: 'Taller del artesano' },
  { value: 'bodega', label: 'Bodega' },
  { value: 'tienda', label: 'Tienda' },
  { value: 'exhibicion', label: 'Exhibición o feria' },
  { value: 'transito', label: 'En tránsito' },
  { value: 'entregada', label: 'Entregada al comprador' },
  { value: 'otro', label: 'Otro' },
];
const locationLabel = (l: string) => LOCATIONS.find((x) => x.value === l)?.label ?? l;

function message(e: unknown): string {
  if (e instanceof ApiError && e.code === 'invalid_location') return 'Revisa los datos: la fecha no puede ser futura.';
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

function today(): string {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

export function UbicacionSection({ piece, onChanged }: { piece: PieceDetail; onChanged: () => void }) {
  const moves = piece.locations ?? [];
  const current = moves[0];
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <section aria-labelledby="location-heading" className="card-elevated p-5 sm:p-6 flex flex-col gap-4">
      <h2 id="location-heading" className="text-xl font-serif text-botanica-negro">Ubicación</h2>
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      {current ? (
        <div className="text-sm flex flex-col gap-1">
          <p className="text-base text-botanica-negro">
            <strong>{locationLabel(current.location)}</strong>{current.place ? ` · ${current.place}` : ''}
          </p>
          <p className="text-botanica-grafito">Desde el {current.moved_on}{current.note ? ` — ${current.note}` : ''}</p>
          <p className="text-xs text-botanica-gris">Registró {current.recorded_by} · {formatDateTime(current.created_at)}</p>
        </div>
      ) : (
        <p className="text-sm text-botanica-grafito">Todavía no se ha registrado dónde está la pieza.</p>
      )}

      {open ? (
        <MoveForm piece={piece} onDone={() => { setOpen(false); onChanged(); }} onCancel={() => setOpen(false)} setError={setError} />
      ) : (
        <div><button type="button" className="btn-secondary" onClick={() => setOpen(true)}>{current ? 'Registrar movimiento' : 'Registrar ubicación'}</button></div>
      )}

      {moves.length > 1 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-botanica-grafito">Movimientos anteriores ({moves.length - 1})</summary>
          <ul className="mt-2 flex flex-col gap-2">
            {moves.slice(1).map((m) => (
              <li key={m.id} className="rounded-lg border border-botanica-gris/20 p-3">
                {m.moved_on} · {locationLabel(m.location)}{m.place ? ` · ${m.place}` : ''}{m.note ? ` — ${m.note}` : ''}
                <span className="block text-xs text-botanica-gris">Registró {m.recorded_by}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function MoveForm({ piece, onDone, onCancel, setError }: {
  piece: PieceDetail; onDone: () => void; onCancel: () => void; setError: (m: string | null) => void;
}) {
  const [location, setLocation] = useState<LocationKind>(piece.locations?.length ? 'tienda' : 'taller');
  const [place, setPlace] = useState('');
  const [movedOn, setMovedOn] = useState(today());
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const input = 'w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white focus:outline-none focus:border-botanica-jade';

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await adminApi.movePiece(piece.id, {
        location, place: place.trim() || null, moved_on: movedOn, note: note.trim() || null,
      }, piece.updated_at);
      toast('Ubicación registrada');
      onDone();
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  }

  return (
    <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <label className="text-xs font-medium text-botanica-grafito">Dónde está
        <select value={location} onChange={(e) => setLocation(e.target.value as LocationKind)} className={input}>
          {LOCATIONS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
        </select>
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Desde
        <input type="date" required max={today()} value={movedOn} onChange={(e) => setMovedOn(e.target.value)} className={input} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito sm:col-span-2">Lugar (opcional)
        <input maxLength={120} value={place} onChange={(e) => setPlace(e.target.value)} className={input}
          placeholder="Ej. Tienda del Andador Turístico, Feria de Cuilápam" />
      </label>
      <label className="text-xs font-medium text-botanica-grafito sm:col-span-2">Nota (opcional)
        <textarea rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} className={input} />
      </label>
      <div className="flex gap-2 sm:col-span-2">
        <button type="submit" className="btn-primary" disabled={busy}>Guardar</button>
        <button type="button" className="btn-secondary" disabled={busy} onClick={onCancel}>Cancelar</button>
      </div>
    </form>
  );
}
