import { useState } from 'react';
import { ApiError, adminApi } from '../api';
import type { Manual, ManualInput, ManualStatus } from '../api';
import { formatDate, writeErrorMessage } from '../format';
import { useConfirm, useToast } from '../feedback-context';
import { useLoad } from '../hooks';
import { useRoles } from '../roles-context';
import { Badge, ErrorState, Loading, PageHeader } from '../ui';

// Training manuals per craft, written and with drawings. Whoever holds the
// Training permission writes them and asks for a review; the owner reads them
// and approves or asks for changes. Drawings are private.

const STATUS: Record<ManualStatus, { label: string; tone: 'jade' | 'neutral' | 'lavanda' }> = {
  draft: { label: 'Borrador', tone: 'neutral' },
  in_review: { label: 'En revisión', tone: 'lavanda' },
  changes_requested: { label: 'Con cambios', tone: 'neutral' },
  approved: { label: 'Aprobado', tone: 'jade' },
};

const CONFLICTS: Record<string, string> = {
  title_required: 'El título necesita al menos 3 letras.',
  craft_required: 'Escribe el oficio (por ejemplo, alfarería o textil).',
  written_required: 'Escribe la versión escrita antes de pedir la revisión.',
  note_required: 'Cuenta qué hay que cambiar (al menos 10 letras).',
  invalid_status: 'Este manual cambió de estado; recarga la página.',
  too_many_images: 'Cada manual admite hasta 12 dibujos.',
  unsupported_type: 'Sube un dibujo JPEG, PNG o WebP.',
  too_large: 'El dibujo pesa más de 25 MB.',
  media_unavailable: 'El servidor todavía no tiene carpeta para imágenes.',
};

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && CONFLICTS[e.code]) return CONFLICTS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

const field = 'w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm text-botanica-negro bg-white focus:outline-none focus:border-botanica-jade';

function ManualForm({ initial, crafts, onSaved, onCancel }: {
  initial?: Manual; crafts: string[]; onSaved: () => void; onCancel: () => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? '');
  const [craft, setCraft] = useState(initial?.craft ?? '');
  const [written, setWritten] = useState(initial?.written ?? '');
  const [visual, setVisual] = useState(initial?.visual ?? '');
  const [nextSteps, setNextSteps] = useState(initial?.next_steps ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  async function save() {
    setBusy(true);
    setError(null);
    const body: ManualInput = {
      title: title.trim(), craft: craft.trim(), written: written.trim() || null, visual: visual.trim() || null,
      next_steps: nextSteps.trim() || null,
    };
    try {
      if (initial) await adminApi.updateManual(initial.id, body);
      else await adminApi.createManual(body);
      toast(initial ? 'Manual actualizado' : 'Manual creado');
      onSaved();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="grid gap-4 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <label className="text-xs font-medium text-botanica-grafito">Título
        <input required minLength={3} maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)}
          placeholder="Ej. Barro negro paso a paso" className={`mt-1 ${field}`} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Oficio
        <input required minLength={2} maxLength={80} list="crafts" value={craft} onChange={(e) => setCraft(e.target.value)}
          placeholder="Ej. Alfarería, Textil, Pintura" className={`mt-1 ${field}`} />
        <datalist id="crafts">{crafts.map((c) => <option key={c} value={c} />)}</datalist>
      </label>
      <label className="text-xs font-medium text-botanica-grafito md:col-span-2">Versión escrita
        <textarea rows={8} value={written} maxLength={20000} onChange={(e) => setWritten(e.target.value)}
          placeholder="El manual completo, paso por paso, con palabras sencillas." className={`mt-1 ${field}`} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito md:col-span-2">Versión con dibujos (el texto que acompaña a los dibujos)
        <textarea rows={5} value={visual} maxLength={20000} onChange={(e) => setVisual(e.target.value)}
          placeholder="Qué muestra cada dibujo. Los dibujos se suben después de guardar." className={`mt-1 ${field}`} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito md:col-span-2">Pendientes y siguientes pasos (opcional)
        <textarea rows={3} value={nextSteps} maxLength={3000} onChange={(e) => setNextSteps(e.target.value)} className={`mt-1 ${field}`} />
      </label>
      {initial && initial.status !== 'draft' && initial.status !== 'changes_requested' && (
        <p className="md:col-span-2 rounded-lg bg-botanica-hueso p-3 text-sm text-botanica-grafito">
          Si cambias algo, el manual vuelve a Borrador y habrá que pedir la revisión otra vez.
        </p>
      )}
      {error && <p role="alert" className="md:col-span-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      <div className="md:col-span-2 flex flex-wrap gap-3">
        <button type="submit" className="btn-primary" disabled={busy}>{initial ? 'Guardar cambios' : 'Crear manual'}</button>
        <button type="button" className="btn-secondary" disabled={busy} onClick={onCancel}>Cancelar</button>
      </div>
    </form>
  );
}

function Images({ manual, onChanged, onError }: { manual: Manual; onChanged: () => void; onError: (m: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [caption, setCaption] = useState('');
  const [busy, setBusy] = useState(false);
  const confirm = useConfirm();
  const toast = useToast();

  async function add() {
    if (!file) return;
    setBusy(true);
    try {
      await adminApi.uploadManualImage(manual.id, file, file.type || 'image/jpeg', caption.trim() || null);
      toast('Dibujo agregado');
      setFile(null);
      setCaption('');
      onChanged();
    } catch (e) {
      onError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    const answer = await confirm({ title: '¿Quitar este dibujo?', body: 'No se puede recuperar.', confirmLabel: 'Quitar dibujo', tone: 'danger' });
    if (answer === null) return;
    try { await adminApi.deleteManualImage(id); toast('Dibujo quitado'); onChanged(); } catch (e) { onError(message(e)); }
  }

  return (
    <div className="mt-3">
      {manual.images.length > 0 && (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3" role="list">
          {manual.images.map((img) => (
            <li key={img.id} className="flex flex-col gap-1">
              <a href={img.url} target="_blank" rel="noopener noreferrer" aria-label={img.caption ?? 'Abrir el dibujo'}>
                <img src={img.url} alt={img.caption ?? `Dibujo del manual ${manual.title}`} loading="lazy"
                  className="aspect-[4/3] w-full rounded-xl border border-botanica-gris/20 object-cover" />
              </a>
              {img.caption && <span className="text-xs text-botanica-grafito">{img.caption}</span>}
              <button type="button" className="text-xs text-red-700 text-left underline" onClick={() => void remove(img.id)}>Quitar</button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="text-xs font-medium text-botanica-grafito">Agregar un dibujo
          <input type="file" accept="image/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className={`mt-1 ${field}`} />
        </label>
        <label className="text-xs font-medium text-botanica-grafito">Pie del dibujo (opcional)
          <input value={caption} maxLength={200} onChange={(e) => setCaption(e.target.value)} className={`mt-1 ${field}`} />
        </label>
        <button type="button" className="btn-secondary" disabled={!file || busy} onClick={() => void add()}>Subir</button>
      </div>
    </div>
  );
}

function Review({ manual, onDone, onError }: { manual: Manual; onDone: () => void; onError: (m: string) => void }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function answer(decision: 'approve' | 'changes') {
    setBusy(true);
    try {
      await adminApi.reviewManual(manual.id, decision, note.trim() || null);
      toast(decision === 'approve' ? 'Manual aprobado' : 'Se pidieron cambios');
      onDone();
    } catch (e) {
      onError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 rounded-xl border border-botanica-gris/20 bg-botanica-hueso p-4">
      <h3 className="text-sm font-medium text-botanica-negro">Tu revisión</h3>
      <label className="mt-2 block text-xs font-medium text-botanica-grafito">Comentarios (obligatorios si pides cambios)
        <textarea rows={3} value={note} maxLength={3000} onChange={(e) => setNote(e.target.value)} className={`mt-1 ${field}`} />
      </label>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className="btn-primary" disabled={busy} onClick={() => void answer('approve')}>Aprobar</button>
        <button type="button" className="btn-secondary" disabled={busy} onClick={() => void answer('changes')}>Pedir cambios</button>
      </div>
    </div>
  );
}

export default function Capacitaciones() {
  const [revision, setRevision] = useState(0);
  const state = useLoad(`manuals:${revision}`, (signal) => adminApi.manuals(signal));
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [error, setError] = useState<string | null>(null);
  const isOwner = useRoles().includes('owner');
  const confirm = useConfirm();
  const toast = useToast();

  if (state.status === 'loading') return <Loading label="Cargando manuales..." />;
  if (state.status === 'error') return <div className="card-elevated"><ErrorState error={state.error} /></div>;
  const all = state.data.data;
  const crafts = [...new Set(all.map((m) => m.craft))].sort((a, b) => a.localeCompare(b, 'es'));
  const shown = filter ? all.filter((m) => m.craft === filter) : all;
  const refresh = () => { setRevision((r) => r + 1); setCreating(false); setEditing(null); setError(null); };

  async function submit(m: Manual) {
    try { await adminApi.submitManual(m.id); toast('Revisión pedida'); refresh(); } catch (e) { setError(message(e)); }
  }

  async function remove(m: Manual) {
    const answer = await confirm({ title: `¿Eliminar «${m.title}»?`, body: 'Se borra el manual y sus dibujos. Queda anotado en la auditoría.', confirmLabel: 'Eliminar manual', tone: 'danger' });
    if (answer === null) return;
    try { await adminApi.deleteManual(m.id); toast('Manual eliminado'); refresh(); } catch (e) { setError(message(e)); }
  }

  return (
    <div className="max-w-4xl mx-auto pb-12 flex flex-col gap-6">
      <PageHeader title="Capacitaciones" subtitle="Manuales por oficio, escritos y con dibujos, que Alexis revisa aquí mismo.">
        {!creating && <button type="button" className="btn-primary" onClick={() => { setCreating(true); setEditing(null); }}>Nuevo manual</button>}
      </PageHeader>

      {creating && (
        <section className="card-elevated p-5 sm:p-6" aria-labelledby="new-manual">
          <h2 id="new-manual" className="text-xl font-serif text-botanica-negro mb-4">Nuevo manual</h2>
          <ManualForm crafts={crafts} onSaved={refresh} onCancel={() => setCreating(false)} />
        </section>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <label className="text-xs font-medium text-botanica-grafito">Oficio
          <select value={filter} onChange={(e) => setFilter(e.target.value)} className="ml-2 text-sm border border-botanica-gris/30 rounded-md px-3 py-1.5 bg-white text-botanica-negro">
            <option value="">Todos</option>
            {crafts.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <span className="text-xs text-botanica-gris" role="status">{shown.length} {shown.length === 1 ? 'manual' : 'manuales'}</span>
      </div>
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      {shown.length === 0 ? (
        <p className="card-elevated p-12 text-center text-botanica-gris">Todavía no hay manuales.</p>
      ) : (
        <ul className="flex flex-col gap-4" role="list">
          {shown.map((m) => (
            <li key={m.id} className="card-elevated p-5 sm:p-6">
              {editing === m.id ? (
                <ManualForm initial={m} crafts={crafts} onSaved={refresh} onCancel={() => setEditing(null)} />
              ) : (
                <article>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-xl font-serif text-botanica-negro">{m.title}</h2>
                    <Badge tone="neutral">{m.craft}</Badge>
                    <Badge tone={STATUS[m.status].tone}>{STATUS[m.status].label}</Badge>
                  </div>
                  <p className="text-xs text-botanica-gris">Creó {m.created_by} · actualizado {formatDate(m.updated_at)}</p>
                  {m.status === 'changes_requested' && m.review_note && (
                    <p className="mt-3 whitespace-pre-line rounded-lg border border-botanica-gris/20 bg-botanica-hueso p-3 text-sm text-botanica-grafito">
                      <strong className="text-botanica-negro">Cambios que pidió {m.reviewed_by}: </strong>{m.review_note}
                    </p>
                  )}
                  {m.status === 'approved' && m.review_note && (
                    <p className="mt-3 whitespace-pre-line text-sm text-botanica-grafito"><strong className="text-botanica-negro">Comentario de la aprobación: </strong>{m.review_note}</p>
                  )}
                  {m.written && (
                    <div className="mt-3">
                      <h3 className="text-sm font-medium text-botanica-negro">Versión escrita</h3>
                      <p className="whitespace-pre-line text-sm text-botanica-grafito">{m.written}</p>
                    </div>
                  )}
                  {(m.visual || m.images.length > 0) && (
                    <div className="mt-3">
                      <h3 className="text-sm font-medium text-botanica-negro">Versión con dibujos</h3>
                      {m.visual && <p className="whitespace-pre-line text-sm text-botanica-grafito">{m.visual}</p>}
                    </div>
                  )}
                  <Images manual={m} onChanged={() => setRevision((r) => r + 1)} onError={setError} />
                  {m.next_steps && (
                    <p className="mt-3 whitespace-pre-line rounded-lg bg-botanica-hueso p-3 text-sm text-botanica-grafito">
                      <strong className="text-botanica-negro">Pendientes y siguientes pasos: </strong>{m.next_steps}
                    </p>
                  )}
                  {isOwner && m.status === 'in_review' && <Review manual={m} onDone={refresh} onError={setError} />}
                  <div className="mt-4 flex flex-wrap gap-2">
                    {(m.status === 'draft' || m.status === 'changes_requested') && (
                      <button type="button" className="btn-primary !py-1.5 text-xs" onClick={() => void submit(m)}>
                        {m.status === 'changes_requested' ? 'Pedir revisión otra vez' : 'Pedir revisión'}
                      </button>
                    )}
                    <button type="button" className="btn-secondary !py-1.5 text-xs" onClick={() => { setEditing(m.id); setCreating(false); }}>Editar</button>
                    {isOwner && <button type="button" className="btn-secondary !py-1.5 text-xs text-red-700 border-red-200" onClick={() => void remove(m)}>Eliminar</button>}
                  </div>
                </article>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
