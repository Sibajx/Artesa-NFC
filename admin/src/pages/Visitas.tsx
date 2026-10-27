import { useState } from 'react';
import { ApiError, adminApi } from '../api';
import { MAX_VISIT_PHOTOS } from '../api';
import type { Visit, VisitInput, VisitKind } from '../api';
import { formatDate, writeErrorMessage } from '../format';
import { useConfirm, useToast } from '../feedback-context';
import { useLoad } from '../hooks';
import { useRoles } from '../roles-context';
import { Badge, ErrorState, Loading, PageHeader } from '../ui';
import { ArtisanPicker } from '../ArtisanPicker';
import type { Picked } from '../ArtisanPicker';

// Visit log of artisans and galleries (Sol and Hariel, plus the owner). The
// written summary is mandatory; photos are optional (up to 8), and private.

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
  too_many_photos: `Una visita admite hasta ${MAX_VISIT_PHOTOS} fotos. Quita una para subir otra.`,
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
  const [who, setWho] = useState<Picked>({ artisanId: initial?.artisan_id ?? '', text: initial?.artisan_name ?? '' });
  const [place, setPlace] = useState(initial?.place ?? '');
  const [attendees, setAttendees] = useState(initial?.attendees ?? '');
  const [summary, setSummary] = useState(initial?.summary ?? '');
  const [agreements, setAgreements] = useState(initial?.agreements ?? '');
  const [consent, setConsent] = useState(initial?.consent_to_publish ?? false);
  const [files, setFiles] = useState<File[]>([]);
  const [fileKey, setFileKey] = useState(0); // a new key clears the file input itself
  const [kept, setKept] = useState(initial?.photos ?? []);
  const room = MAX_VISIT_PHOTOS - kept.length - files.length;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  async function save() {
    setBusy(true);
    setError(null);
    const body: VisitInput = {
      visited_on: date, kind, artisan_id: who.artisanId || null, unregistered_artisan: who.artisanId ? null : who.text.trim() || null, place: place.trim() || null, attendees: attendees.trim() || null,
      summary: summary.trim(), agreements: agreements.trim() || null, consent_to_publish: consent,
    };
    try {
      const saved = initial ? await adminApi.updateVisit(initial.id, body) : await adminApi.createVisit(body);
      const failed: string[] = [];
      let reason = '';
      for (const file of files) {
        try { await adminApi.uploadVisitPhoto(saved.id, file, file.type || 'image/jpeg'); } catch (e) { failed.push(file.name); reason = message(e); }
      }
      if (failed.length > 0) {
        // The visit itself is saved: closing the form keeps it from being created twice.
        toast(`La visita se guardó, pero ${failed.length === 1 ? 'una foto no se subió' : `${failed.length} fotos no se subieron`} (${failed.join(', ')}): ${reason} Edita la visita para volver a subirlas.`);
      } else {
        toast(initial ? 'Visita actualizada' : 'Visita registrada');
      }
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
      <div className="text-xs font-medium text-botanica-grafito">
        <label htmlFor="visit-artisan">Artesano visitado (si aplica)</label>
        <div className="mt-1">
          <ArtisanPicker id="visit-artisan" artisans={artisans} value={who} onChange={setWho} className={field} />
        </div>
      </div>
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
      <div className="md:col-span-2 flex flex-col gap-2">
        <label className="text-xs font-medium text-botanica-grafito">
          Fotos de la visita (opcional; no tiene que salir el artesano; hasta {MAX_VISIT_PHOTOS})
          <input key={fileKey} type="file" accept="image/*" multiple disabled={room <= 0}
            onChange={(e) => { setFiles((prev) => [...prev, ...Array.from(e.target.files ?? [])].slice(0, MAX_VISIT_PHOTOS - kept.length)); setFileKey((k) => k + 1); }}
            className={`mt-1 ${field}`} />
        </label>
        {room < 0 && <p role="alert" className="text-xs text-red-700">Son demasiadas fotos: quita {-room} para guardar.</p>}
        {(kept.length > 0 || files.length > 0) && (
          <ul className="flex flex-wrap gap-2" role="list" aria-label="Fotos de esta visita">
            {kept.map((p) => (
              <li key={p.id} className="relative">
                <img src={p.url} alt="Foto guardada de la visita" loading="lazy" className="h-20 w-20 rounded-lg border border-botanica-gris/20 object-cover" />
                <button type="button" disabled={busy} aria-label="Quitar esta foto guardada"
                  className="absolute -right-1.5 -top-1.5 grid h-6 w-6 place-items-center rounded-full bg-botanica-negro text-xs text-white"
                  onClick={() => { void (async () => {
                    if (!initial) return;
                    try { await adminApi.deleteVisitPhoto(initial.id, p.id); setKept((k) => k.filter((x) => x.id !== p.id)); toast('Foto quitada'); } catch (e) { setError(message(e)); }
                  })(); }}>×</button>
              </li>
            ))}
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="flex max-w-[11rem] items-center gap-2 rounded-lg border border-botanica-gris/30 bg-botanica-hueso px-2 py-1 text-xs text-botanica-grafito">
                <span className="truncate" title={f.name}>{f.name}</span>
                <button type="button" disabled={busy} aria-label={`Quitar ${f.name}`} className="shrink-0 font-medium text-botanica-negro"
                  onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}>Quitar</button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <label className="flex items-start gap-2 text-sm text-botanica-grafito md:col-span-2">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-1 accent-botanica-jade" />
        <span>La persona visitada autorizó que se publique algo de esta visita. <span className="text-botanica-gris">Solo se anota; por ahora nada de esto se publica.</span></span>
      </label>
      {error && <p role="alert" className="md:col-span-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      <div className="md:col-span-2 flex flex-wrap gap-3">
        <button type="submit" className="btn-primary" disabled={busy || room < 0}>{initial ? 'Guardar cambios' : 'Registrar visita'}</button>
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

  async function removePhoto(v: Visit, photoId: string) {
    const answer = await confirm({ title: '¿Borrar esta foto de la visita?', body: 'No se puede recuperar.', confirmLabel: 'Borrar foto', tone: 'danger' });
    if (answer === null) return;
    try { await adminApi.deleteVisitPhoto(v.id, photoId); toast('Foto borrada'); setRevision((r) => r + 1); } catch (e) { setError(message(e)); }
  }

  async function remove(v: Visit) {
    const answer = await confirm({ title: '¿Eliminar esta visita?', body: 'Se borra el registro y sus fotos. Queda anotado en la auditoría.', confirmLabel: 'Eliminar visita', tone: 'danger' });
    if (answer === null) return;
    try { await adminApi.deleteVisit(v.id); toast('Visita eliminada'); setRevision((r) => r + 1); } catch (e) { setError(message(e)); }
  }

  return (
    <div className="max-w-4xl mx-auto pb-12 flex flex-col gap-6">
      <PageHeader title="Visitas" subtitle="Bitácora de visitas a artesanos y galerías: qué se habló, qué se acordó y fotos.">
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
                  {v.photos.length > 0 && (
                    <ul className="flex shrink-0 flex-wrap gap-2 sm:w-[17rem]" role="list" aria-label="Fotos de la visita">
                      {v.photos.map((p, i) => (
                        <li key={p.id} className="relative">
                          <a href={p.url} target="_blank" rel="noopener noreferrer" aria-label={`Abrir la foto ${i + 1} de la visita`}>
                            <img src={p.url} alt={`Foto ${i + 1} de la visita del ${formatDate(v.visited_on)}`} loading="lazy" className={`${v.photos.length === 1 ? 'h-32 w-32' : 'h-20 w-20'} rounded-xl border border-botanica-gris/20 object-cover`} />
                          </a>
                          <button type="button" aria-label={`Quitar la foto ${i + 1}`} onClick={() => void removePhoto(v, p.id)}
                            className="absolute -right-1.5 -top-1.5 grid h-6 w-6 place-items-center rounded-full bg-botanica-negro text-xs text-white">×</button>
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-xl font-serif text-botanica-negro">{formatDate(v.visited_on)}</h2>
                      <Badge tone="neutral">{kindLabel(v.kind)}</Badge>
                      {v.consent_to_publish && <Badge tone="jade">Autorizó publicar</Badge>}
                    </div>
                    <p className="text-sm text-botanica-grafito">
                      {[v.artisan_name && !v.artisan_id ? `${v.artisan_name} (no registrado)` : v.artisan_name, v.place].filter(Boolean).join(' · ') || 'Sin lugar'}
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
