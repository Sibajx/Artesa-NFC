import { useRef, useState } from 'react';
import { ApiError, adminApi } from './api';
import type { ProductionStep, ProductionStepKey } from './api';
import { formatDateTime, writeErrorMessage } from './format';
import { useConfirm, useToast } from './feedback-context';
import { useLoad } from './hooks';
import { Badge, ErrorState, Gate, Loading } from './ui';

// The way of a piece from the artisan to the buyer, like a parcel tracker:
// received, chip placed, chip programmed (automatic, from Certificación), packed,
// shipped, delivered. Photos are private evidence (never public media).

const TITLES: Record<ProductionStepKey, { title: string; hint: string }> = {
  received: { title: 'Recibida del artesano', hint: 'La pieza llegó al equipo.' },
  chip_placed: { title: 'Chip colocado', hint: 'El chip NFC ya está dentro o sobre la pieza.' },
  chip_programmed: { title: 'Chip programado y probado', hint: 'Se marca solo cuando Certificación programa el chip.' },
  packed: { title: 'Embalada', hint: 'Lista para salir, con su tarjeta.' },
  shipped: { title: 'Enviada', hint: 'Salió con la paquetería.' },
  delivered: { title: 'Entregada', hint: 'La recibió el comprador.' },
};

const CONFLICTS: Record<string, string> = {
  already_done: 'Ese paso ya estaba marcado. Recarga la pieza.',
  step_not_done: 'Primero marca ese paso.',
  too_many_photos: 'Cada paso admite hasta 5 fotos.',
  invalid_step: 'La paquetería y la guía solo van en el envío.',
  automatic_step: 'Ese paso se marca solo desde Certificación.',
  unsupported_type: 'Sube una foto JPEG, PNG o WebP.',
  too_large: 'La foto pesa más de 25 MB.',
  media_unavailable: 'El servidor todavía no tiene carpeta para fotos.',
};

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && CONFLICTS[e.code]) return CONFLICTS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

const field = 'w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm text-botanica-negro bg-white focus:outline-none focus:border-botanica-jade';

function StepRow({ pieceId, s, last, photosEnabled, onChanged }: {
  pieceId: string; s: ProductionStep; last: boolean; photosEnabled: boolean; onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [carrier, setCarrier] = useState('');
  const [tracking, setTracking] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const markFile = useRef<HTMLInputElement>(null);
  const addFile = useRef<HTMLInputElement>(null);
  const confirm = useConfirm();
  const toast = useToast();
  const info = TITLES[s.step];

  async function run(call: () => Promise<unknown>, done: string): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await call();
      toast(done);
      onChanged();
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function mark() {
    const file = markFile.current?.files?.[0];
    const ok = await run(async () => {
      await adminApi.markProductionStep(pieceId, s.step, {
        note: note.trim() || null,
        carrier: s.step === 'shipped' ? carrier.trim() || null : null,
        tracking: s.step === 'shipped' ? tracking.trim() || null : null,
      });
      if (file) await adminApi.uploadProductionPhoto(pieceId, s.step, file, file.type || 'image/jpeg');
    }, `${info.title}: marcado`);
    if (ok) { setOpen(false); setNote(''); setCarrier(''); setTracking(''); }
  }

  async function addPhoto(file: File | undefined) {
    if (!file) return;
    await run(() => adminApi.uploadProductionPhoto(pieceId, s.step, file, file.type || 'image/jpeg'), 'Foto agregada');
    if (addFile.current) addFile.current.value = '';
  }

  async function undo() {
    const answer = await confirm({
      title: `¿Quitar «${info.title}»?`,
      body: 'Se borra la marca y también sus fotos. Queda registrado en la auditoría.',
      confirmLabel: 'Quitar paso', tone: 'danger',
    });
    if (answer !== null) await run(() => adminApi.undoProductionStep(pieceId, s.step), 'Paso quitado');
  }

  async function removePhoto(url: string) {
    const answer = await confirm({ title: '¿Borrar esta foto?', body: 'No se puede recuperar.', confirmLabel: 'Borrar foto', tone: 'danger' });
    if (answer !== null) await run(() => adminApi.deleteProductionPhoto(url), 'Foto borrada');
  }

  const manual = s.step !== 'chip_programmed';
  return (
    <li className="relative flex gap-4 pb-6 last:pb-0">
      {!last && <span aria-hidden="true" className="absolute left-[11px] top-6 bottom-0 w-0.5 bg-botanica-gris/25" />}
      <span aria-hidden="true" className={`relative z-10 mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border-2 text-xs ${
        s.done ? 'border-botanica-jade bg-botanica-jade text-[var(--on-accent)]' : 'border-botanica-gris/40 bg-white text-transparent'}`}>✓</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className={`text-base font-sans font-semibold ${s.done ? 'text-botanica-negro' : 'text-botanica-gris'}`}>{info.title}</h3>
          {s.done && s.source === 'auto' && <Badge tone="neutral">Automático</Badge>}
        </div>
        {s.done ? (
          <p className="text-xs text-botanica-gris">
            {s.done_at && formatDateTime(s.done_at)}{s.done_by && <> · {s.done_by}</>}
          </p>
        ) : <p className="text-xs text-botanica-gris">{info.hint}</p>}
        {s.note && <p className="mt-1 text-sm text-botanica-grafito">{s.note}</p>}
        {(s.carrier || s.tracking) && (
          <p className="mt-1 text-sm text-botanica-grafito">{s.carrier}{s.carrier && s.tracking && ' · '}{s.tracking && <>Guía <strong className="tabular-nums">{s.tracking}</strong></>}</p>
        )}
        {s.photos.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-2" role="list">
            {s.photos.map((url, i) => (
              <li key={url} className="relative">
                <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`Abrir foto ${i + 1} de ${info.title}`}>
                  <img src={url} alt={`Foto ${i + 1}: ${info.title}`} loading="lazy" className="h-20 w-20 rounded-lg border border-botanica-gris/20 object-cover" />
                </a>
                <Gate permission="logistics">
                  <button type="button" disabled={busy} onClick={() => void removePhoto(url)} aria-label={`Borrar foto ${i + 1}`}
                    className="absolute -right-1.5 -top-1.5 grid h-6 w-6 place-items-center rounded-full bg-botanica-negro text-xs text-[var(--bg)]">×</button>
                </Gate>
              </li>
            ))}
          </ul>
        )}
        {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}

        {manual && (
          <Gate permission="logistics">
            {!s.done && !open && (
              <button type="button" className="btn-primary mt-3 !py-2" onClick={() => setOpen(true)}>Marcar este paso</button>
            )}
            {!s.done && open && (
              <form className="mt-3 flex flex-col gap-3 rounded-xl border border-botanica-gris/20 p-4" onSubmit={(e) => { e.preventDefault(); void mark(); }}>
                {s.step === 'shipped' && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-xs font-medium text-botanica-grafito">Paquetería
                      <input value={carrier} maxLength={80} onChange={(e) => setCarrier(e.target.value)} placeholder="Ej. Estafeta, DHL" className={`mt-1 ${field}`} />
                    </label>
                    <label className="text-xs font-medium text-botanica-grafito">Número de guía
                      <input value={tracking} maxLength={80} onChange={(e) => setTracking(e.target.value)} className={`mt-1 ${field}`} />
                    </label>
                  </div>
                )}
                <label className="text-xs font-medium text-botanica-grafito">Nota (opcional)
                  <input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} className={`mt-1 ${field}`} />
                </label>
                {photosEnabled && (
                  <label className="text-xs font-medium text-botanica-grafito">Foto (opcional)
                    <input ref={markFile} type="file" accept="image/*" capture="environment" className={`mt-1 ${field}`} />
                  </label>
                )}
                <div className="flex flex-wrap gap-3">
                  <button type="submit" className="btn-primary" disabled={busy}>Guardar paso</button>
                  <button type="button" className="btn-secondary" disabled={busy} onClick={() => setOpen(false)}>Cancelar</button>
                </div>
              </form>
            )}
            {s.done && (
              <div className="mt-2 flex flex-wrap gap-2">
                {photosEnabled && s.photos.length < 5 && (
                  <label className="btn-secondary cursor-pointer !py-1.5 text-xs">
                    Agregar foto
                    <input ref={addFile} type="file" accept="image/*" capture="environment" className="sr-only" disabled={busy}
                      onChange={(e) => void addPhoto(e.target.files?.[0])} />
                  </label>
                )}
                <button type="button" className="btn-secondary !py-1.5 text-xs" disabled={busy} onClick={() => void undo()}>Quitar paso</button>
              </div>
            )}
          </Gate>
        )}
      </div>
    </li>
  );
}

export function ProductionTimeline({ pieceId, onChanged }: { pieceId: string; onChanged?: () => void }) {
  const [revision, setRevision] = useState(0);
  const state = useLoad(`production:${pieceId}:${revision}`, (signal) => adminApi.productionPiece(pieceId, signal));
  if (state.status === 'loading') return <Loading label="Cargando seguimiento..." />;
  if (state.status === 'error') return <ErrorState error={state.error} />;
  const { steps, photos_enabled } = state.data;
  return (
    <ol className="flex flex-col" aria-label="Pasos de producción">
      {steps.map((s, i) => (
        <StepRow key={s.step} pieceId={pieceId} s={s} last={i === steps.length - 1} photosEnabled={photos_enabled}
          onChanged={() => { setRevision((r) => r + 1); onChanged?.(); }} />
      ))}
    </ol>
  );
}
