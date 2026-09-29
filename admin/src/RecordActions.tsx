import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, adminApi } from './api';
import type { PublicationStatus, Transition } from './api';
import { labels, writeErrorMessage } from './format';
import { FormError } from './forms';

interface Props {
  kind: 'artisans' | 'pieces';
  id: string;
  version: string;
  status: PublicationStatus;
  availability?: string;
  onChanged: () => void;
}

const CONFIRM: Partial<Record<Transition, string>> = {
  unpublish: '¿Pasar a borrador? Dejará de verse en el sitio público.',
  publish: '¿Publicar? Se verá en el sitio público (las piezas, solo si su artesano también está publicado).',
};

export function RecordActions({ kind, id, version, status, availability, onChanged }: Props) {
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const base = kind === 'artisans' ? '/artesanos' : '/piezas';

  async function run(call: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await call();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('network', 0));
    } finally {
      setBusy(false);
    }
  }

  function transition(action: Transition) {
    let reason: string | undefined;
    if (action === 'archive') {
      const answer = window.prompt('Motivo del archivo (queda en la auditoría):', '');
      if (answer === null) return;
      reason = answer.trim() || undefined;
    } else if (CONFIRM[action] && !window.confirm(CONFIRM[action])) {
      return;
    }
    void run(() => kind === 'artisans'
      ? adminApi.transitionArtisan(id, version, action, reason)
      : adminApi.transitionPiece(id, version, action, reason));
  }

  const buttons: { action: Transition; label: string; primary?: boolean }[] =
    status === 'draft' ? [{ action: 'publish', label: 'Publicar', primary: true }, { action: 'archive', label: 'Archivar' }]
      : status === 'published' ? [{ action: 'unpublish', label: 'Pasar a borrador' }, { action: 'archive', label: 'Archivar' }]
        : [{ action: 'restore', label: 'Restaurar como borrador' }];

  return (
    <div className="flex flex-col gap-3">
      <FormError message={error ? writeErrorMessage(error) : null} />
      <div className="flex flex-wrap items-center gap-3">
        {status !== 'archived' && <Link to={`${base}/${id}/editar`} className="btn-secondary">Editar</Link>}
        {buttons.map((b) => (
          <button key={b.action} type="button" disabled={busy} onClick={() => transition(b.action)}
            className={b.primary ? 'btn-primary' : 'btn-secondary'}>
            {b.label}
          </button>
        ))}
        {kind === 'pieces' && availability && status !== 'archived' && (
          <label className="flex items-center gap-2 text-sm text-botanica-grafito ml-auto">
            Disponibilidad
            <select value={availability} disabled={busy}
              onChange={(e) => void run(() => adminApi.setAvailability(id, version, e.target.value))}
              className="text-sm border border-botanica-gris/30 rounded-md px-3 py-1.5 bg-white text-botanica-negro focus:outline-none focus:border-botanica-jade">
              {['available', 'reserved', 'exhibited', 'archived'].map((a) => <option key={a} value={a}>{labels.availability(a)}</option>)}
            </select>
          </label>
        )}
      </div>
    </div>
  );
}
