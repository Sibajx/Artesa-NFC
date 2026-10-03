import { useState } from 'react';
import { ApiError, adminApi } from './api';
import type { PieceDetail } from './api';
import { writeErrorMessage } from './format';
import { useToast } from './feedback-context';

// ADR-030 phase 4: the piece's palette for its public certificate. Filled
// automatically from the cover photo on the first upload; it can be
// regenerated from the cover or adjusted by hand (3-5 colours).

const MIN = 3;
const MAX = 5;

const CONFLICTS: Record<string, string> = {
  no_cover_photo: 'La pieza no tiene fotos activas de las que sacar colores. Sube una foto primero.',
  unreadable_cover_photo: 'No se pudo leer la foto de portada. Prueba con otra.',
  invalid_palette: 'Usa de 3 a 5 colores.',
};

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && CONFLICTS[e.code]) return CONFLICTS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

function paletteOf(piece: PieceDetail): string[] {
  const value = (piece.visual_theme as { palette?: unknown } | null)?.palette;
  return Array.isArray(value) ? value.filter((c): c is string => typeof c === 'string') : [];
}

export function PaletteSection({ piece, onChanged }: { piece: PieceDetail; onChanged: () => void }) {
  const current = paletteOf(piece);
  const source = (piece.visual_theme as { palette_source?: string } | null)?.palette_source;
  const [draft, setDraft] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const shown = draft ?? current;

  async function run(call: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError(null);
    try {
      await call();
      setDraft(null);
      toast(done);
      onChanged();
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  }

  return (
    <section aria-labelledby="palette-heading" className="card-elevated p-5 sm:p-6 flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="palette-heading" className="text-xl font-serif text-botanica-negro">Colores del certificado</h2>
        {current.length > 0 && !draft && (
          <span className="text-xs text-botanica-gris">
            {source === 'manual' ? 'Elegidos a mano' : 'Sacados de la foto de portada'}
          </span>
        )}
      </div>
      <p className="text-sm text-botanica-grafito">
        Dan color al certificado público que se ve al escanear el chip. Se toman solos de la foto de portada; puedes
        ajustarlos.
      </p>

      {shown.length > 0 ? (
        <div className="flex flex-wrap gap-3" aria-label="Paleta">
          {shown.map((color, i) => (
            <div key={i} className="flex flex-col items-center gap-1.5">
              {draft ? (
                <input
                  type="color"
                  aria-label={`Color ${i + 1}`}
                  value={color}
                  onChange={(e) => setDraft(draft.map((c, j) => (j === i ? e.target.value : c)))}
                  className="h-14 w-14 cursor-pointer rounded-xl border border-botanica-gris/30 bg-transparent p-0.5"
                />
              ) : (
                <span className="h-14 w-14 rounded-xl border border-botanica-gris/20 shadow-sm" style={{ background: color }} />
              )}
              <span className="font-mono text-xs text-botanica-grafito">{color}</span>
              {draft && draft.length > MIN && (
                <button type="button" className="text-xs text-botanica-grafito underline" onClick={() => setDraft(draft.filter((_, j) => j !== i))}>
                  Quitar
                </button>
              )}
            </div>
          ))}
          {draft && draft.length < MAX && (
            <button type="button" className="h-14 w-14 rounded-xl border border-dashed border-botanica-gris/50 text-2xl text-botanica-grafito"
              aria-label="Agregar color" onClick={() => setDraft([...draft, draft[draft.length - 1] ?? '#888888'])}>+</button>
          )}
        </div>
      ) : (
        <p className="text-sm text-botanica-gris">Aún no tiene colores. Se generan al subir la primera foto.</p>
      )}

      {shown.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-botanica-gris/20" aria-hidden="true">
          <div className="flex h-3">{shown.map((c, i) => <span key={i} className="flex-1" style={{ background: c }} />)}</div>
          <div className="px-4 py-3 text-xs text-botanica-grafito" style={{ background: `color-mix(in srgb, ${shown[shown.length - 1]} 12%, white)` }}>
            Así se verá la franja del certificado de <strong>{piece.name}</strong>.
          </div>
        </div>
      )}

      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      <div className="flex flex-wrap gap-2">
        {draft ? (
          <>
            <button type="button" className="btn-primary" disabled={busy}
              onClick={() => void run(() => adminApi.setPalette(piece.id, draft, piece.updated_at), 'Colores guardados')}>
              Guardar colores
            </button>
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => { setDraft(null); setError(null); }}>Cancelar</button>
          </>
        ) : (
          <>
            <button type="button" className="btn-secondary" disabled={busy}
              onClick={() => void run(() => adminApi.generatePalette(piece.id, piece.updated_at), 'Colores tomados de la portada')}>
              {current.length > 0 ? 'Volver a sacar de la portada' : 'Sacar de la portada'}
            </button>
            {current.length > 0 && (
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => setDraft([...current])}>Ajustar a mano</button>
            )}
          </>
        )}
      </div>
    </section>
  );
}
