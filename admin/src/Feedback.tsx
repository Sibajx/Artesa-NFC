import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { FeedbackContext } from './feedback-context';
import type { ConfirmOptions } from './feedback-context';

interface Pending extends ConfirmOptions {
  resolve: (value: string | null) => void;
}

interface ToastItem {
  id: number;
  message: string;
  tone: 'success' | 'error';
}

let toastId = 0;

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const confirm = useCallback(
    (options: ConfirmOptions) => new Promise<string | null>((resolve) => setPending({ ...options, resolve })),
    [],
  );

  const toast = useCallback((message: string, tone: 'success' | 'error' = 'success') => {
    const id = ++toastId;
    setToasts((list) => [...list, { id, message, tone }]);
    window.setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), 4000);
  }, []);

  const value = useMemo(() => ({ confirm, toast }), [confirm, toast]);

  return (
    <FeedbackContext.Provider value={value}>
      {children}
      {pending && (
        <ConfirmDialog options={pending} onClose={(result) => { pending.resolve(result); setPending(null); }} />
      )}
      <div className="toast-region" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast--${t.tone}`}>
            <span aria-hidden="true">{t.tone === 'success' ? '✓' : '!'}</span>
            {t.message}
          </div>
        ))}
      </div>
    </FeedbackContext.Provider>
  );
}

function ConfirmDialog({ options, onClose }: { options: ConfirmOptions; onClose: (result: string | null) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState('');

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  const danger = options.tone === 'danger';
  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby="confirm-title" aria-describedby={options.body ? 'confirm-body' : undefined}
      onCancel={(e) => { e.preventDefault(); onClose(null); }}
      onClick={(e) => { if (e.target === ref.current) onClose(null); }}>
      <form method="dialog" className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); onClose(reason.trim()); }}>
        <div className="flex items-start gap-3">
          <span aria-hidden="true" className={`confirm-dialog__icon ${danger ? 'confirm-dialog__icon--danger' : ''}`}>
            {danger ? '!' : '?'}
          </span>
          <div className="flex flex-col gap-1">
            <h2 id="confirm-title" className="text-xl font-serif text-botanica-negro">{options.title}</h2>
            {options.body && <p id="confirm-body" className="text-sm text-botanica-grafito">{options.body}</p>}
          </div>
        </div>
        {options.reason && (
          <label className="flex flex-col gap-1 text-xs font-medium text-botanica-grafito">
            {options.reason.label}
            <input className="input-base" value={reason} onChange={(e) => setReason(e.target.value)}
              placeholder={options.reason.placeholder} maxLength={200} />
          </label>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={() => onClose(null)}>{options.cancelLabel ?? 'Cancelar'}</button>
          <button type="submit" autoFocus className={danger ? 'btn-danger' : 'btn-primary'}>{options.confirmLabel}</button>
        </div>
      </form>
    </dialog>
  );
}
