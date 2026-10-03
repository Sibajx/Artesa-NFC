import { useState } from 'react';
import type { PublicationStatus } from './api';
import { usePublish } from './usePublish';

// "Publicar" right in a list row, for drafts only.
export function QuickPublish({ kind, id, version, name, status, onChanged }: {
  kind: 'artisans' | 'pieces'; id: string; version: string; name: string; status: PublicationStatus; onChanged: () => void;
}) {
  const publish = usePublish();
  const [busy, setBusy] = useState(false);
  if (status !== 'draft') return null;
  return (
    <button type="button" className="btn-primary !py-1.5 !px-3 text-xs" disabled={busy}
      aria-label={`Publicar ${name}`}
      onClick={() => {
        setBusy(true);
        void publish({ kind, id, version, name }).then((changed) => {
          setBusy(false);
          if (changed) onChanged();
        });
      }}>
      Publicar
    </button>
  );
}
