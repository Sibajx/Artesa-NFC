import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, adminApi } from './api';
import type { PublicationStatus, Transition } from './api';
import { PURGE_BLOCKERS, formatDateTime, labels, writeErrorMessage } from './format';
import { FormError } from './forms';

interface Props {
  kind: 'artisans' | 'pieces';
  id: string;
  version: string;
  status: PublicationStatus;
  availability?: string;
  trashedAt?: string | null;
  purgeBlocker?: string | null;
  onChanged: () => void;
}

const CONFIRM: Partial<Record<Transition, string>> = {
  unpublish: '¿Pasar a borrador? Dejará de verse en el sitio público.',
  publish: '¿Publicar? Se verá en el sitio público (las piezas, solo si su artesano también está publicado).',
};

export function RecordActions({ kind, id, version, status, availability, trashedAt, purgeBlocker, onChanged }: Props) {
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const base = kind === 'artisans' ? '/artesanos' : '/piezas';
  const noun = kind === 'artisans' ? 'este artesano' : 'esta pieza';

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

  function toTrash() {
    if (!window.confirm(`¿Enviar ${noun} a la papelera? Dejará de aparecer en las listas; podrás restaurarlo desde «Papelera».`)) return;
    void run(() => adminApi.trashAction(kind, id, version, 'trash'));
  }

  function purge() {
    if (!window.confirm(`¿Eliminar ${noun} definitivamente, con todas sus fotos? No se puede deshacer.`)) return;
    void run(async () => {
      await adminApi.purge(kind, id, version);
      navigate('/papelera');
    });
  }

  if (trashedAt) {
    return (
      <section aria-label="En la papelera" className="rounded-xl border border-red-200 bg-red-50/60 p-5 flex flex-col gap-3">
        <FormError message={error ? writeErrorMessage(error) : null} />
        <div>
          <p className="font-medium text-botanica-negro">En la papelera desde el {formatDateTime(trashedAt)}.</p>
          <p className="text-sm text-botanica-grafito mt-1">
            {purgeBlocker ? PURGE_BLOCKERS[purgeBlocker] ?? 'No se puede eliminar definitivamente.' : 'Puedes restaurarlo o eliminarlo definitivamente.'}
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <button type="button" disabled={busy} className="btn-primary"
            onClick={() => void run(() => adminApi.trashAction(kind, id, version, 'untrash'))}>Restaurar</button>
          {!purgeBlocker && (
            <button type="button" disabled={busy} onClick={purge}
              className="btn-secondary text-red-700 border-red-200 hover:bg-red-50">Eliminar definitivamente</button>
          )}
        </div>
      </section>
    );
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
        {status !== 'published' && (
          <button type="button" disabled={busy} onClick={toTrash}
            className="btn-secondary text-red-700 border-red-200 hover:bg-red-50">Mover a la papelera</button>
        )}
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
