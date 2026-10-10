import { useState } from 'react';
import { ApiError, adminApi } from '../api';
import type { Visit, VisitInput, VisitKind } from '../api';
import { formatDate, writeErrorMessage } from '../format';
import { useConfirm, useToast } from '../feedback-context';
import { useLoad } from '../hooks';
import { useRoles } from '../roles-context';
import { Badge, ErrorState, Loading, PageHeader } from '../ui';

// Visit log of artisans and galleries (Sol and Hariel, plus the owner). The
// written summary is mandatory; the photo is one and optional, and private.

const KINDS: { value: VisitKind; label: string }[] = [
  { value: 'artesano', label: 'Artesano' },
  { value: 'galeria', label: 'Galería' },
  { value: 'otro', label: 'Otro' },
];
const kindLabel = (k: string) => KINDS.find((x) => x.value === k)?.label ?? k;

const CONFLICTS: Record<string, string> = {
  summary_required: 'Escribe el resumen de lo que pasó (al menos 10 letras).',
  place_required: 'Escribe el nombre de la galería.',
  invalid_visit: 'La fecha de la visita no puede ser futura.',
  unsupported_type: 'Sube una foto JPEG, PNG o WebP.',
  too_large: 'La foto pesa más de 25 MB.',
  media_unavailable: 'El servidor todavía no tiene carpeta para fotos.',
};

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && CONFLICTS[e.code]) return CONFLICTS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

const today = () => {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
};
const field = 'w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm text-botanica-negro bg-white focus:outline-none focus:border-botanica-jade';

function VisitForm({ initial, artisans, onSaved, onCancel }: {
  initial?: Visit; artisans: { id: string; full_name: string }[]; onSaved: () => void; onCancel: () => void;
}) {
  const [date, setDate] = useState(initial?.visited_on ?? today());
  const [kind, setKind] = useState<VisitKind>(initial?.kind ?? 'artesano');
  const [artisanId, setArtisanId] = useState(initial?.artisan_id ?? '');
  const [place, setPlace] = useState(initial?.place ?? '');
  const [attendees, setAttendees] = useState(initial?.attendees ?? '');
  const [summary, setSummary] = useState(initial?.summary ?? '');
  const [agreements, setAgreements] = useState(initial?.agreements ?? '');
  const [consent, setConsent] = useState(initial?.consent_to_publish ?? false);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  async function save() {
    setBusy(true);
    setError(null);
    const body: VisitInput = {
      visited_on: date, kind, artisan_id: artisanId || null, place: place.trim() || null, attendees: attendees.trim() || null,
      summary: summary.trim(), agreements: agreements.trim() || null, consent_to_publish: consent,
    };
    try {
      const saved = initial ? await adminApi.updateVisit(initial.id, body) : await adminApi.createVisit(body);
      if (file) await adminApi.uploadVisitPhoto(saved.id, file, file.type || 'image/jpeg');
      toast(initial ? 'Visita actualizada' : 'Visita registrada');
      onSaved();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="grid gap-4 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <label className="text-xs font-medium text-botanica-grafito">Fecha de la visita
        <input type="date" required max={today()} value={date} onChange={(e) => setDate(e.target.value)} className={`mt-1 ${field}`} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Con quién
        <select value={kind} onChange={(e) => setKind(e.target.value as VisitKind)} className={`mt-1 ${field}`}>
          {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
        </select>
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Artesano (si aplica)
        <select value={artisanId} onChange={(e) => setArtisanId(e.target.value)} className={`mt-1 ${field}`}>
          <option value="">Ninguno</option>
          {artisans.map((a) => <option key={a.id} value={a.id}>{a.full_name}</option>)}
        </select>
      </label>
      <label className="text-xs font-medium text-botanica-grafito">{kind === 'galeria' ? 'Galería (obligatorio)' : 'Lugar (opcional)'}
        <input value={place} maxLength={200} required={kind === 'galeria'} onChange={(e) => setPlace(e.target.value)} placeholder="Ej. Taller en San Bartolo, Galería Centro" className={`mt-1 ${field}`} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito md:col-span-2">Quién fue del equipo (opcional)
        <input value={attendees} maxLength={300} onChange={(e) => setAttendees(e.target.value)} placeholder="Ej. Sol y Hariel" className={`mt-1 ${field}`} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito md:col-span-2">Resumen de lo que pasó (obligatorio)
        <textarea required rows={5} value={summary} maxLength={5000} onChange={(e) => setSummary(e.target.value)}
          placeholder="De qué se habló, qué se vio, cómo estuvo la visita." className={`mt-1 ${field}`} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito md:col-span-2">Acuerdos y siguientes pasos (opcional)
        <textarea rows={3} value={agreements} maxLength={3000} onChange={(e) => setAgreements(e.target.value)} className={`mt-1 ${field}`} />
      </label>
      <label className="text-xs font-medium text-botanica-grafito md:col-span-2">
        {initial?.photo ? 'Cambiar la foto (opcional)' : 'Una foto de la visita (opcional; no tiene que salir el artesano)'}
        <input type="file" accept="image/*" capture="environment" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className={`mt-1 ${field}`} />
      </label>
      <label className="flex items-start gap-2 text-sm text-botanica-grafito md:col-span-2">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-1 accent-botanica-jade" />
        <span>La persona visitada autorizó que se publique algo de esta visita. <span className="text-botanica-gris">Solo se anota; por ahora nada de esto se publica.</span></span>
      </label>
      {error && <p role="alert" className="md:col-span-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      <div className="md:col-span-2 flex flex-wrap gap-3">
        <button type="submit" className="btn-primary" disabled={busy}>{initial ? 'Guardar cambios' : 'Registrar visita'}</button>
        <button type="button" className="btn-secondary" disabled={busy} onClick={onCancel}>Cancelar</button>
      </div>
    </form>
  );
}

export default function Visitas() {
  const [revision, setRevision] = useState(0);
  const [filter, setFilter] = useState('');
  const state = useLoad(`visits:${revision}:${filter}`, (signal) => adminApi.visits(filter || undefined, signal));
  const artisans = useLoad('artisans-for-visits', (signal) => adminApi.artisans({}, signal));
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isOwner = useRoles().includes('owner');
  const confirm = useConfirm();
  const toast = useToast();

  if (state.status === 'loading') return <Loading label="Cargando visitas..." />;
  if (state.status === 'error') return <div className="card-elevated"><ErrorState error={state.error} /></div>;
  const list = artisans.status === 'ready' ? artisans.data.data.map((a) => ({ id: a.id, full_name: a.full_name })) : [];
  const refresh = () => { setRevision((r) => r + 1); setCreating(false); setEditing(null); };

  async function removePhoto(v: Visit) {
    const answer = await confirm({ title: '¿Borrar la foto de esta visita?', body: 'No se puede recuperar.', confirmLabel: 'Borrar foto', tone: 'danger' });
    if (answer === null) return;
    try { await adminApi.deleteVisitPhoto(v.id); toast('Foto borrada'); setRevision((r) => r + 1); } catch (e) { setError(message(e)); }
  }

  async function remove(v: Visit) {
    const answer = await confirm({ title: '¿Eliminar esta visita?', body: 'Se borra el registro y su foto. Queda anotado en la auditoría.', confirmLabel: 'Eliminar visita', tone: 'danger' });
    if (answer === null) return;
    try { await adminApi.deleteVisit(v.id); toast('Visita eliminada'); setRevision((r) => r + 1); } catch (e) { setError(message(e)); }
  }

  return (
    <div className="max-w-4xl mx-auto pb-12 flex flex-col gap-6">
      <PageHeader title="Visitas" subtitle="Bitácora de visitas a artesanos y galerías: qué se habló, qué se acordó y una foto.">
        {!creating && <button type="button" className="btn-primary" onClick={() => { setCreating(true); setEditing(null); }}>Nueva visita</button>}
      </PageHeader>

      {creating && (
        <section className="card-elevated p-5 sm:p-6" aria-labelledby="new-visit">
          <h2 id="new-visit" className="text-xl font-serif text-botanica-negro mb-4">Nueva visita</h2>
          <VisitForm artisans={list} onSaved={refresh} onCancel={() => setCreating(false)} />
        </section>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <label className="text-xs font-medium text-botanica-grafito">Ver las visitas de
          <select value={filter} onChange={(e) => setFilter(e.target.value)} className="ml-2 text-sm border border-botanica-gris/30 rounded-md px-3 py-1.5 bg-white text-botanica-negro">
            <option value="">Todos</option>
            {list.map((a) => <option key={a.id} value={a.id}>{a.full_name}</option>)}
          </select>
        </label>
        <span className="text-xs text-botanica-gris" role="status">{state.data.data.length} {state.data.data.length === 1 ? 'visita' : 'visitas'}</span>
      </div>
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      {state.data.data.length === 0 ? (
        <p className="card-elevated p-12 text-center text-botanica-gris">Todavía no hay visitas registradas.</p>
      ) : (
        <ul className="flex flex-col gap-4" role="list">
          {state.data.data.map((v) => (
            <li key={v.id} className="card-elevated p-5 sm:p-6">
              {editing === v.id ? (
                <VisitForm initial={v} artisans={list} onSaved={refresh} onCancel={() => setEditing(null)} />
              ) : (
                <article className="flex flex-col gap-3 sm:flex-row sm:gap-5">
                  {v.photo && (
                    <a href={v.photo} target="_blank" rel="noopener noreferrer" className="shrink-0" aria-label="Abrir la foto de la visita">
                      <img src={v.photo} alt={`Foto de la visita del ${formatDate(v.visited_on)}`} loading="lazy" className="h-32 w-32 rounded-xl border border-botanica-gris/20 object-cover" />
                    </a>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-xl font-serif text-botanica-negro">{formatDate(v.visited_on)}</h2>
                      <Badge tone="neutral">{kindLabel(v.kind)}</Badge>
                      {v.consent_to_publish && <Badge tone="jade">Autorizó publicar</Badge>}
                    </div>
                    <p className="text-sm text-botanica-grafito">
                      {[v.artisan_name, v.place].filter(Boolean).join(' · ') || 'Sin lugar'}
                      {v.attendees && <span className="text-botanica-gris"> · fue: {v.attendees}</span>}
                    </p>
                    <p className="mt-2 whitespace-pre-line text-sm text-botanica-grafito">{v.summary}</p>
                    {v.agreements && (
                      <p className="mt-2 whitespace-pre-line rounded-lg bg-botanica-hueso p-3 text-sm text-botanica-grafito">
                        <strong className="text-botanica-negro">Acuerdos y siguientes pasos: </strong>{v.agreements}
                      </p>
                    )}
                    <p className="mt-2 text-xs text-botanica-gris">Registró {v.recorded_by}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button type="button" className="btn-secondary !py-1.5 text-xs" onClick={() => { setEditing(v.id); setCreating(false); }}>Editar</button>
                      {v.photo && <button type="button" className="btn-secondary !py-1.5 text-xs" onClick={() => void removePhoto(v)}>Quitar foto</button>}
                      {isOwner && <button type="button" className="btn-secondary !py-1.5 text-xs text-red-700 border-red-200" onClick={() => void remove(v)}>Eliminar</button>}
                    </div>
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
