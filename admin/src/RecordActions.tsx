import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, adminApi } from './api';
import type { PublicationStatus, Transition } from './api';
import { PURGE_BLOCKERS, formatDateTime, labels, writeErrorMessage } from './format';
import { FormError } from './forms';
import { useConfirm, useToast } from './feedback-context';
import { usePublish } from './usePublish';
import type { PublishTarget } from './usePublish';

interface Props {
  kind: 'artisans' | 'pieces';
  id: string;
  version: string;
  status: PublicationStatus;
  availability?: string;
  trashedAt?: string | null;
  purgeBlocker?: string | null;
  /** Display name for confirmations and toasts. */
  name: string;
  /** Artisans: their draft pieces, for "Publicar artesano y sus piezas". */
  draftPieces?: PublishTarget[];
  onChanged: () => void;
}

export function RecordActions({ kind, id, version, status, availability, trashedAt, purgeBlocker, name, draftPieces = [], onChanged }: Props) {
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const confirm = useConfirm();
  const toast = useToast();
  const publish = usePublish();
  const self: PublishTarget = { kind, id, version, name };
  const base = kind === 'artisans' ? '/artesanos' : '/piezas';

  async function run(call: () => Promise<unknown>, done?: string) {
    setBusy(true);
    setError(null);
    try {
      await call();
      if (done) toast(done);
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('network', 0));
    } finally {
      setBusy(false);
    }
  }

  async function doPublish(pieces: PublishTarget[] = [], artisanPublished = false) {
    setBusy(true);
    if (await publish(self, pieces, artisanPublished)) onChanged();
    setBusy(false);
  }

  async function transition(action: Transition) {
    if (action === 'publish') return doPublish();
    let reason: string | undefined;
    if (action === 'archive') {
      const answer = await confirm({
        title: `¿Archivar «${name}»?`,
        body: 'Dejará de verse en el sitio y pasará a «Archivados». Podrás restaurarlo como borrador.',
        confirmLabel: 'Archivar',
        reason: { label: 'Motivo (queda en la auditoría, opcional)', placeholder: 'Ej.: pieza vendida, datos por corregir…' },
      });
      if (answer === null) return;
      reason = answer || undefined;
    } else if (action === 'unpublish') {
      const answer = await confirm({
        title: `¿Pasar «${name}» a borrador?`,
        body: 'Dejará de verse en el sitio público hasta que lo vuelvas a publicar.',
        confirmLabel: 'Pasar a borrador',
      });
      if (answer === null) return;
    }
    const done = action === 'archive' ? `«${name}» archivado` : action === 'unpublish' ? `«${name}» pasó a borrador` : `«${name}» restaurado como borrador`;
    void run(() => kind === 'artisans'
      ? adminApi.transitionArtisan(id, version, action, reason)
      : adminApi.transitionPiece(id, version, action, reason), done);
  }

  async function toTrash() {
    const answer = await confirm({
      title: `¿Enviar «${name}» a la papelera?`,
      body: 'Dejará de aparecer en las listas. Podrás restaurarlo desde «Papelera».',
      confirmLabel: 'Mover a la papelera',
      tone: 'danger',
    });
    if (answer === null) return;
    void run(() => adminApi.trashAction(kind, id, version, 'trash'), `«${name}» está en la papelera`);
  }

  async function purge() {
    const answer = await confirm({
      title: `¿Eliminar «${name}» definitivamente?`,
      body: 'Se borran el registro y todas sus fotos. No se puede deshacer.',
      confirmLabel: 'Eliminar definitivamente',
      tone: 'danger',
    });
    if (answer === null) return;
    void run(async () => {
      await adminApi.purge(kind, id, version);
      navigate('/papelera');
    }, `«${name}» eliminado`);
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
            onClick={() => void run(() => adminApi.trashAction(kind, id, version, 'untrash'), `«${name}» restaurado`)}>Restaurar</button>
          {!purgeBlocker && (
            <button type="button" disabled={busy} onClick={() => void purge()} className="btn-danger">Eliminar definitivamente</button>
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
    <div className="record-bar flex flex-col gap-3">
      <FormError message={error ? writeErrorMessage(error) : null} />
      <div className="flex flex-wrap items-center gap-3">
        {status === 'draft' && (
          <button type="button" disabled={busy} onClick={() => void doPublish()} className="btn-primary btn-publish">
            Publicar
          </button>
        )}
        {kind === 'artisans' && status !== 'archived' && draftPieces.length > 0 && (
          <button type="button" disabled={busy} onClick={() => void doPublish(draftPieces, status === 'published')}
            className={status === 'draft' ? 'btn-secondary' : 'btn-primary btn-publish'}>
            {status === 'draft'
              ? `Publicar con ${draftPieces.length === 1 ? 'su pieza' : `sus ${draftPieces.length} piezas`}`
              : `Publicar ${draftPieces.length === 1 ? 'su pieza en borrador' : `sus ${draftPieces.length} piezas en borrador`}`}
          </button>
        )}
        {status !== 'archived' && <Link to={`${base}/${id}/editar`} className="btn-secondary">Editar</Link>}
        {buttons.filter((b) => b.action !== 'publish').map((b) => (
          <button key={b.action} type="button" disabled={busy} onClick={() => void transition(b.action)} className="btn-secondary">
            {b.label}
          </button>
        ))}
        {status !== 'published' && (
          <button type="button" disabled={busy} onClick={() => void toTrash()}
            className="btn-secondary text-red-700 border-red-200 hover:bg-red-50">Mover a la papelera</button>
        )}
        {kind === 'pieces' && availability && status !== 'archived' && (
          <label className="flex items-center gap-2 text-sm text-botanica-grafito ml-auto">
            Disponibilidad
            <select value={availability} disabled={busy}
              onChange={(e) => void run(() => adminApi.setAvailability(id, version, e.target.value), 'Disponibilidad actualizada')}
              className="text-sm border border-botanica-gris/30 rounded-md px-3 py-1.5 bg-white text-botanica-negro focus:outline-none focus:border-botanica-jade">
              {['available', 'reserved', 'exhibited', 'archived'].map((a) => <option key={a} value={a}>{labels.availability(a)}</option>)}
            </select>
          </label>
        )}
      </div>
    </div>
  );
}
