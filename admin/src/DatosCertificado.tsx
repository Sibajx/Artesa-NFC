import { useState } from 'react';
import { ApiError, adminApi } from './api';
import type { CustodyAuthenticity } from './api';
import { writeErrorMessage } from './format';
import { useToast } from './feedback-context';

// PEND-030: what the public certificate shows about how the piece was
// verified and which edition it is. The server cuts nothing silently: too long
// or a future date come back as a field error.

const ERRORS: Record<string, string> = {
  verified_by_too_long: 'La verificación es demasiado larga (máximo 160 letras).',
  edition_too_long: 'La edición es demasiado larga (máximo 60 letras).',
  notes_too_long: 'La nota es demasiado larga (máximo 280 letras).',
  verified_on_in_the_future: 'La fecha de verificación no puede ser futura.',
  no_active_certificate: 'La pieza no tiene certificado activo. Recarga la página.',
};

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && ERRORS[e.code]) return ERRORS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

function today(): string {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

const INPUT = 'w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white focus:outline-none focus:border-botanica-jade';

interface Props {
  pieceId: string;
  current: CustodyAuthenticity | null;
  reload: () => void;
}

export default function DatosCertificado({ pieceId, current, reload }: Props) {
  const [verifiedBy, setVerifiedBy] = useState(current?.verified_by ?? '');
  const [verifiedOn, setVerifiedOn] = useState(current?.verified_on ?? '');
  const [edition, setEdition] = useState(current?.edition ?? '');
  const [notes, setNotes] = useState(current?.notes ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await adminApi.custodyAuthenticity(pieceId, {
        verified_by: verifiedBy.trim() || null,
        verified_on: verifiedOn || null,
        edition: edition.trim() || null,
        notes: notes.trim() || null,
      });
      toast('Datos del certificado guardados');
      reload();
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  }

  return (
    <section aria-labelledby="authenticity-heading" className="card-elevated p-5 sm:p-6 flex flex-col gap-3 text-sm">
      <h2 id="authenticity-heading" className="text-lg font-serif text-botanica-negro">Datos que muestra el certificado</h2>
      <p className="text-botanica-grafito">
        Esto lo ve cualquiera que escanee la pieza, junto al certificado. Déjalo vacío lo que no aplique. Se conserva si reemplazas el certificado.
      </p>
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-red-800">{error}</p>}
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <label className="text-xs font-medium text-botanica-grafito sm:col-span-2">Verificación: cómo y quién
          <input maxLength={160} value={verifiedBy} onChange={(e) => setVerifiedBy(e.target.value)} className={INPUT}
            placeholder="Verificada en persona en el taller por Sol y Alexis" />
        </label>
        <label className="text-xs font-medium text-botanica-grafito">Fecha de la verificación
          <input type="date" max={today()} value={verifiedOn} onChange={(e) => setVerifiedOn(e.target.value)} className={INPUT} />
        </label>
        <label className="text-xs font-medium text-botanica-grafito">Edición
          <input maxLength={60} value={edition} onChange={(e) => setEdition(e.target.value)} className={INPUT}
            placeholder="Pieza única, o 3 de 10" />
        </label>
        <label className="text-xs font-medium text-botanica-grafito sm:col-span-2">Nota corta (opcional)
          <textarea maxLength={280} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} className={INPUT}
            placeholder="Certificado en el marco del caso piloto de Cuilápam" />
        </label>
        <div className="sm:col-span-2">
          <button type="submit" className="btn-primary" disabled={busy}>{busy ? 'Guardando…' : 'Guardar'}</button>
        </div>
      </form>
    </section>
  );
}
