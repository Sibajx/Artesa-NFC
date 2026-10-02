import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, adminApi } from '../api';
import type { ArtisanSummary, PieceSummary } from '../api';
import { formatDate, formatDateTime, writeErrorMessage } from '../format';
import { FormError } from '../forms';
import { useLoad } from '../hooks';
import { ErrorState, Loading, PageHeader, PublicationBadge } from '../ui';

// Two "apartados" of the sidebar sharing one layout:
// - Archivados: records archived (publication state), restorable to draft
//   from their page;
// - Papelera: records sent to the trash (services/trash.py), restorable
//   here, deletable for good from their page when they were never public.

type Mode = 'archivados' | 'papelera';

const COPY: Record<Mode, { title: string; subtitle: string; empty: string }> = {
  archivados: {
    title: 'Archivados',
    subtitle: 'Retirados del sitio sin borrarse. Desde su página puedes restaurarlos como borrador.',
    empty: 'No hay nada archivado.',
  },
  papelera: {
    title: 'Papelera',
    subtitle: 'Lo que enviaste a la papelera. Restáuralo, o elimínalo definitivamente desde su página si nunca se publicó.',
    empty: 'La papelera está vacía.',
  },
};

export default function Apartado({ mode }: { mode: Mode }) {
  const [revision, setRevision] = useState(0);
  const params = mode === 'papelera' ? { trashed: 'true' } : { publication_status: 'archived' };
  const state = useLoad(`${mode}:${revision}`, async (signal) => {
    const [artisans, pieces] = await Promise.all([adminApi.artisans(params, signal), adminApi.pieces(params, signal)]);
    return { artisans: artisans.data, pieces: pieces.data };
  });
  const copy = COPY[mode];

  return (
    <div className="max-w-6xl mx-auto pb-12">
      <PageHeader title={copy.title} subtitle={copy.subtitle} />
      {state.status === 'loading' && <Loading label={`Cargando ${copy.title.toLowerCase()}...`} />}
      {state.status === 'error' && <div className="card-elevated"><ErrorState error={state.error} /></div>}
      {state.status === 'ready' && (
        state.data.artisans.length === 0 && state.data.pieces.length === 0 ? (
          <p className="p-12 text-center text-botanica-gris bg-white rounded-xl border border-botanica-gris/20">{copy.empty}</p>
        ) : (
          <div className="flex flex-col gap-8">
            <Group title={`Artesanos (${state.data.artisans.length})`}>
              {state.data.artisans.map((a) => (
                <Row key={a.id} mode={mode} kind="artisans" record={a} title={a.full_name}
                  detail={`${a.piece_count} pieza${a.piece_count === 1 ? '' : 's'}`} onChanged={() => setRevision((r) => r + 1)} />
              ))}
            </Group>
            <Group title={`Piezas (${state.data.pieces.length})`}>
              {state.data.pieces.map((p) => (
                <Row key={p.id} mode={mode} kind="pieces" record={p} title={p.name}
                  detail={p.public_code} onChanged={() => setRevision((r) => r + 1)} />
              ))}
            </Group>
          </div>
        )
      )}
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode[] }) {
  return (
    <section>
      <h2 className="text-2xl font-serif text-botanica-negro mb-3">{title}</h2>
      {children.length === 0 ? (
        <p className="p-6 text-sm text-botanica-gris bg-white rounded-xl border border-botanica-gris/15">Ninguno.</p>
      ) : (
        <ul className="bg-white border border-botanica-gris/15 rounded-xl overflow-hidden shadow-sm divide-y divide-botanica-gris/10">{children}</ul>
      )}
    </section>
  );
}

function Row({ mode, kind, record, title, detail, onChanged }: {
  mode: Mode; kind: 'artisans' | 'pieces'; record: ArtisanSummary | PieceSummary; title: string; detail: string; onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const path = `/${kind === 'artisans' ? 'artesanos' : 'piezas'}/${record.id}`;

  async function restore() {
    setBusy(true);
    setError(null);
    try {
      await adminApi.trashAction(kind, record.id, record.updated_at, 'untrash');
      onChanged();
    } catch (e) {
      setError(writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="p-4 px-6 flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-col">
        <Link to={path} className="font-medium text-botanica-negro hover:text-botanica-jade">{title}</Link>
        <span className="text-xs text-botanica-gris">
          {detail} · {mode === 'papelera' && record.trashed_at
            ? `en la papelera desde ${formatDateTime(record.trashed_at)}`
            : `actualizado ${formatDate(record.updated_at)}`}
        </span>
        <FormError message={error} />
      </div>
      <div className="flex items-center gap-3">
        <PublicationBadge status={record.publication_status} />
        {mode === 'papelera' ? (
          <>
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => void restore()}>Restaurar</button>
            <Link to={path} className="btn-secondary">Abrir</Link>
          </>
        ) : (
          <Link to={path} className="btn-secondary">Abrir para restaurar</Link>
        )}
      </div>
    </li>
  );
}
