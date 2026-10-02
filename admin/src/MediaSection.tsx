import { useEffect, useMemo, useState } from 'react';
import type { DragEvent, FormEvent } from 'react';
import { ApiError, MAX_UPLOAD_BYTES, adminApi, mediaUrl } from './api';
import type { AdminMedia, MediaRole } from './api';
import { labels, writeErrorMessage } from './format';
import { FormError, Select, TextArea } from './forms';
import { PhotoEditor } from './PhotoEditor';
import { Badge } from './ui';

type Kind = 'artisans' | 'pieces';

// Roles each owner accepts (backend app/services/media.py ROLES).
const ROLES: Record<Kind, MediaRole[]> = {
  artisans: ['portrait', 'process', 'gallery'],
  pieces: ['hero', 'gallery', 'detail', 'process', 'model_3d'],
};

// Which roles take which kind of file (same table as the backend).
const ROLE_TYPES: Record<MediaRole, string[]> = {
  portrait: ['image'],
  hero: ['image'],
  detail: ['image'],
  gallery: ['image', 'video'],
  process: ['image', 'video'],
  model_3d: ['model_3d'],
};

// Backend limit of the alternative text (schemas/admin_write.py AltText).
const ALT_MAX = 300;
const ALT_HINT = `Qué se ve en la foto, para quien no puede verla. Máximo ${ALT_MAX} caracteres; la historia larga de la pieza va en su descripción.`;

const ACCEPT = 'image/jpeg,image/png,image/webp,video/mp4,.glb,model/gltf-binary';

// Browsers give .glb files an empty type; the API sniffs the bytes anyway.
function contentTypeOf(file: Blob & { name?: string }): string {
  if (file.type) return file.type;
  return file.name?.toLowerCase().endsWith('.glb') ? 'model/gltf-binary' : 'application/octet-stream';
}

function isPhoto(file: (Blob & { name?: string }) | null): boolean {
  return !!file && (file.type.startsWith('image/') || /\.(jpe?g|png|webp)$/i.test(file.name ?? ''));
}

function altError(alt: string, required: boolean): string | null {
  if (required && !alt.trim()) return 'Las fotos necesitan una descripción (texto alternativo).';
  if (alt.trim().length > ALT_MAX) {
    return `La descripción tiene ${alt.trim().length} caracteres; el máximo es ${ALT_MAX}. Resúmela: la historia completa va en la descripción de la pieza.`;
  }
  return null;
}

function messageFor(e: unknown): string {
  const error = e instanceof ApiError ? e : new ApiError('network', 0);
  // A 422 from request validation names the field instead of a code.
  if (error.kind === 'invalid' && !error.code && error.fields.alt_text) {
    return `La descripción es demasiado larga (máximo ${ALT_MAX} caracteres).`;
  }
  return writeErrorMessage(error);
}

function Counter({ value }: { value: string }) {
  const n = value.trim().length;
  return (
    <p className={`-mt-2 text-xs text-right tabular-nums ${n > ALT_MAX ? 'text-red-700 font-medium' : 'text-botanica-gris'}`}
      aria-live="polite">
      {n}/{ALT_MAX}
    </p>
  );
}

// An edited photo keeps a name so the list stays readable.
function asFile(blob: Blob, name: string): File {
  return new File([blob], name.replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' });
}

// A local preview URL that is released when the file changes or unmounts.
function usePreview(file: Blob | null): string | null {
  const url = useMemo(() => (file && isPhoto(file) ? URL.createObjectURL(file) : null), [file]);
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  return url;
}

interface Props {
  kind: Kind;
  ownerId: string;
  media: AdminMedia[];
  ownerArchived: boolean;
  onChanged: () => void;
}

export function MediaSection({ kind, ownerId, media, ownerArchived, onChanged }: Props) {
  const active = media.filter((m) => m.status === 'active').length;
  return (
    <section aria-labelledby={`media-heading-${ownerId}`}>
      <h2 id={`media-heading-${ownerId}`} className="text-2xl font-serif text-botanica-negro mb-2">
        Medios ({active}{media.length > active ? ` + ${media.length - active} archivados` : ''})
      </h2>
      <p className="text-sm text-botanica-gris mb-4">
        Puedes subir varias fotos a la vez y recortarlas o rotarlas antes de subirlas. Al subir se les quitan los
        metadatos (incluida la ubicación GPS) y se reducen a 1600 px; el original se guarda aparte, en privado.
        Arrastra las tarjetas para cambiar el orden. Cada medio se puede <strong>editar</strong> o <strong>reemplazar</strong>;
        si nunca se vio en el sitio, también <strong>eliminar</strong>; si ya pudo verse, se <strong>archiva</strong>.
      </p>
      {!ownerArchived && <UploadForm kind={kind} ownerId={ownerId} onUploaded={onChanged} />}
      {media.length === 0 ? (
        <p className="p-8 text-center text-botanica-gris bg-white border border-botanica-gris/15 rounded-xl">Sin medios todavía.</p>
      ) : (
        <MediaList kind={kind} ownerId={ownerId} media={media} ownerArchived={ownerArchived} onChanged={onChanged} />
      )}
    </section>
  );
}

// --- upload (several files at once) -------------------------------------------------

interface Pending {
  key: number;
  file: File;
  role: MediaRole;
  alt: string;
  state: 'ready' | 'uploading' | 'error';
  error?: string;
}

let pendingKey = 0;

function UploadForm({ kind, ownerId, onUploaded }: { kind: Kind; ownerId: string; onUploaded: () => void }) {
  const roles = ROLES[kind];
  const [items, setItems] = useState<Pending[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [editing, setEditing] = useState<Pending | null>(null);

  const update = (key: number, patch: Partial<Pending>) =>
    setItems((list) => list.map((p) => (p.key === key ? { ...p, ...patch } : p)));

  function add(files: FileList | null) {
    if (!files) return;
    const added = [...files].map((file): Pending => ({ key: ++pendingKey, file, role: roles[0], alt: '', state: 'ready' }));
    setItems((list) => [...list, ...added]);
    setInputKey((k) => k + 1);
    setError(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (items.length === 0) {
      setError('Elige uno o varios archivos.');
      return;
    }
    // Validate everything first, so nothing is half-uploaded by a typo.
    let invalid = false;
    for (const p of items) {
      const problem = p.file.size > MAX_UPLOAD_BYTES ? 'El archivo pesa más de 25 MB.' : altError(p.alt, isPhoto(p.file));
      update(p.key, problem ? { state: 'error', error: problem } : { state: 'ready', error: undefined });
      if (problem) invalid = true;
    }
    if (invalid) {
      setError('Revisa los archivos marcados.');
      return;
    }
    setBusy(true);
    setError(null);
    let uploaded = 0;
    for (const p of items) {
      update(p.key, { state: 'uploading' });
      try {
        await adminApi.uploadMedia(kind, ownerId, p.file, contentTypeOf(p.file), p.role, p.alt.trim() || undefined);
        uploaded += 1;
        setItems((list) => list.filter((x) => x.key !== p.key));
      } catch (e) {
        update(p.key, { state: 'error', error: messageFor(e) });
      }
    }
    setBusy(false);
    if (uploaded > 0) onUploaded();
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="bg-white border border-botanica-gris/15 rounded-xl p-6 mb-4 flex flex-col gap-4 shadow-sm">
      <h3 className="text-lg font-serif text-botanica-negro">Subir medios</h3>
      <FormError message={error} />
      <div>
        <label htmlFor={`media-file-${ownerId}`} className="block text-xs font-medium text-botanica-grafito mb-1">
          Archivos
        </label>
        <input key={inputKey} id={`media-file-${ownerId}`} type="file" accept={ACCEPT} multiple disabled={busy}
          onChange={(e) => add(e.target.files)}
          aria-describedby={`media-file-${ownerId}-hint`}
          className="block w-full text-sm text-botanica-grafito file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border file:border-botanica-gris/30 file:bg-white file:text-sm file:text-botanica-negro" />
        <p id={`media-file-${ownerId}-hint`} className="mt-1 text-xs text-botanica-gris">
          Puedes elegir varios a la vez. Foto JPEG, PNG o WebP (hasta 25 MB) · video MP4 sin sonido ni ubicación (hasta 4 MB) · modelo 3D GLB (hasta 8 MB).
        </p>
      </div>

      {items.length > 0 && (
        <ul className="flex flex-col gap-4">
          {items.map((p) => (
            <PendingRow key={p.key} item={p} roles={roles} busy={busy}
              onChange={(patch) => update(p.key, patch)}
              onEdit={() => setEditing(p)}
              onRemove={() => setItems((list) => list.filter((x) => x.key !== p.key))} />
          ))}
        </ul>
      )}

      <div>
        <button type="submit" disabled={busy || items.length === 0} className="btn-primary">
          {busy ? 'Subiendo…' : items.length > 1 ? `Subir ${items.length} archivos` : 'Subir'}
        </button>
      </div>

      {editing && (
        <PhotoEditor source={editing.file}
          onCancel={() => setEditing(null)}
          onDone={(blob) => { update(editing.key, { file: asFile(blob, editing.file.name) }); setEditing(null); }} />
      )}
    </form>
  );
}

function PendingRow({ item, roles, busy, onChange, onEdit, onRemove }: {
  item: Pending; roles: MediaRole[]; busy: boolean;
  onChange: (patch: Partial<Pending>) => void; onEdit: () => void; onRemove: () => void;
}) {
  const preview = usePreview(item.file);
  const photo = isPhoto(item.file);
  const id = `pending-${item.key}`;
  return (
    <li className={`grid grid-cols-1 md:grid-cols-[140px_1fr] gap-4 p-4 rounded-lg border ${item.state === 'error' ? 'border-red-300 bg-red-50/40' : 'border-botanica-gris/15'}`}>
      <div className="flex flex-col gap-2">
        {preview
          ? <img src={preview} alt="" className="w-full aspect-square object-cover rounded-md bg-botanica-hueso" />
          : <div className="w-full aspect-square rounded-md bg-botanica-hueso flex items-center justify-center text-xs text-botanica-grafito p-2 text-center">{item.file.name}</div>}
        {photo && <button type="button" className="btn-secondary text-xs" disabled={busy} onClick={onEdit}>Recortar o rotar</button>}
      </div>
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-medium text-botanica-negro break-all">{item.file.name}</p>
          <button type="button" className="text-xs underline text-botanica-grafito" disabled={busy} onClick={onRemove}>Quitar</button>
        </div>
        {item.state === 'uploading' && <p className="text-xs text-botanica-jade" aria-live="polite">Subiendo…</p>}
        {item.error && <p role="alert" className="text-xs text-red-700">{item.error}</p>}
        <Select id={`${id}-role`} label="Uso" value={item.role} disabled={busy}
          onChange={(v) => onChange({ role: v as MediaRole })}
          options={roles.map((r) => ({ value: r, label: labels.role(r) }))} />
        <TextArea id={`${id}-alt`} label="Descripción (texto alternativo)" value={item.alt} disabled={busy}
          onChange={(v) => onChange({ alt: v })} required={photo} hint={ALT_HINT}
          placeholder="Máscara de madera tallada, vista de frente, con pintura roja y negra" />
        <Counter value={item.alt} />
      </div>
    </li>
  );
}

// --- list with drag-and-drop ordering ---------------------------------------------------

function byPosition(media: AdminMedia[]): AdminMedia[] {
  return [...media].sort((a, b) => a.media.position - b.media.position);
}

function MediaList({ kind, ownerId, media, ownerArchived, onChanged }: Props) {
  const [order, setOrder] = useState<AdminMedia[]>(() => byPosition(media));
  const [loaded, setLoaded] = useState(media);
  const [dragId, setDragId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announce, setAnnounce] = useState('');

  // A fresh load from the server replaces the local order (adjusting state
  // during render, as React recommends, instead of in an effect).
  if (loaded !== media) {
    setLoaded(media);
    setOrder(byPosition(media));
  }

  async function persist(next: AdminMedia[]) {
    setOrder(next);
    setSaving(true);
    setError(null);
    try {
      // Keep each saved item's new version, so a quick second move does not
      // send a stale If-Match before the reload arrives.
      const saved = [...next];
      for (const [i, item] of next.entries()) {
        if (item.media.position !== i) saved[i] = await adminApi.updateMedia(item.id, item.updated_at, { position: i });
      }
      setOrder(saved);
      setAnnounce('Orden guardado.');
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setSaving(false);
      onChanged();
    }
  }

  function move(from: number, to: number) {
    if (to < 0 || to >= order.length || from === to) return;
    const next = [...order];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    void persist(next);
  }

  function onDrop(e: DragEvent, targetIndex: number) {
    e.preventDefault();
    const from = order.findIndex((m) => m.id === dragId);
    setDragId(null);
    if (from >= 0) move(from, targetIndex);
  }

  return (
    <>
      <FormError message={error} />
      <p className="sr-only" aria-live="polite">{saving ? 'Guardando orden…' : announce}</p>
      {saving && <p className="text-sm text-botanica-jade mb-2">Guardando orden…</p>}
      <ul className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {order.map((m, i) => (
          <li key={m.id}
            draggable={!saving}
            onDragStart={(e) => { setDragId(m.id); e.dataTransfer.effectAllowed = 'move'; }}
            onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }}
            onDrop={(e) => onDrop(e, i)}
            onDragEnd={() => setDragId(null)}
            className={`rounded-xl transition-shadow ${dragId === m.id ? 'opacity-50' : ''} ${dragId && dragId !== m.id ? 'ring-1 ring-dashed ring-botanica-jade/40' : ''}`}>
            <MediaCard kind={kind} ownerId={ownerId} item={m} ownerArchived={ownerArchived} onChanged={onChanged}
              index={i} count={order.length} disabled={saving}
              onMove={(to) => move(i, to)} />
          </li>
        ))}
      </ul>
    </>
  );
}

// --- one media item ---------------------------------------------------------------------

function Preview({ item }: { item: AdminMedia }) {
  const { media } = item;
  const src = mediaUrl(media.url);
  if (media.type === 'image') {
    return <img src={src} alt={media.alt_text ?? ''} loading="lazy" draggable={false} className="w-full aspect-[4/3] object-cover bg-botanica-hueso" />;
  }
  if (media.type === 'video') {
    return <video src={src} controls muted playsInline preload="metadata" className="w-full aspect-[4/3] bg-botanica-negro" />;
  }
  return (
    <div className="w-full aspect-[4/3] bg-botanica-hueso flex items-center justify-center text-botanica-grafito text-sm">
      <a href={src} className="underline hover:text-botanica-jade">Modelo 3D (GLB)</a>
    </div>
  );
}

type Mode = 'view' | 'edit' | 'replace';

function MediaCard({ kind, ownerId, item, ownerArchived, onChanged, index, count, disabled, onMove }: {
  kind: Kind; ownerId: string; item: AdminMedia; ownerArchived: boolean; onChanged: () => void;
  index: number; count: number; disabled: boolean; onMove: (to: number) => void;
}) {
  const [mode, setMode] = useState<Mode>('view');
  const [alt, setAlt] = useState(item.media.alt_text ?? '');
  const [role, setRole] = useState<MediaRole>(item.media.role as MediaRole);
  const [file, setFile] = useState<File | null>(null);
  const [editorSource, setEditorSource] = useState<Blob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const archived = item.status === 'archived';
  const isImage = item.media.type === 'image';
  const roleOptions = ROLES[kind].filter((r) => ROLE_TYPES[r].includes(item.media.type));
  const newPreview = usePreview(file);
  const locked = busy || disabled;

  async function run(call: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await call();
      setMode('view');
      setFile(null);
      onChanged();
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setMode('view');
    setError(null);
    setAlt(item.media.alt_text ?? '');
    setRole(item.media.role as MediaRole);
    setFile(null);
  }

  function save(event: FormEvent) {
    event.preventDefault();
    const invalidAlt = altError(alt, isImage);
    if (invalidAlt) {
      setError(invalidAlt);
      return;
    }
    const body: { alt_text: string | null; role?: MediaRole } = { alt_text: alt.trim() || null };
    if (role !== item.media.role) body.role = role;
    void run(() => adminApi.updateMedia(item.id, item.updated_at, body));
  }

  // Starts from the published photo (≤ 1600 px). For the best quality,
  // upload the original again and crop that one instead.
  async function editCurrent() {
    setError(null);
    try {
      const response = await fetch(mediaUrl(item.media.url));
      if (!response.ok) throw new Error(String(response.status));
      setEditorSource(await response.blob());
    } catch {
      setError('No se pudo abrir la foto actual. Elige el archivo desde tu equipo.');
    }
  }

  // Replace = upload the new file with the same use, description and order,
  // then remove the old one: deleted when it was never public, archived
  // otherwise (a published file is never overwritten in place).
  function replace(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError('Elige el archivo nuevo o recorta la foto actual.');
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError('El archivo pesa más de 25 MB.');
      return;
    }
    const invalidAlt = altError(alt, isPhoto(file));
    if (invalidAlt) {
      setError(invalidAlt);
      return;
    }
    void run(async () => {
      const added = await adminApi.uploadMedia(kind, ownerId, file, contentTypeOf(file), item.media.role as MediaRole, alt.trim() || undefined);
      if (added.media.position !== item.media.position) {
        await adminApi.updateMedia(added.id, added.updated_at, { position: item.media.position });
      }
      if (item.deletable) await adminApi.deleteMedia(item.id, item.updated_at);
      else if (!archived) await adminApi.transitionMedia(item.id, item.updated_at, 'archive');
    });
  }

  function toggleArchive() {
    const question = archived
      ? '¿Restaurar este medio? Volverá a verse en el sitio si el registro está publicado.'
      : '¿Archivar este medio? Dejará de verse en el sitio; el archivo no se borra.';
    if (!window.confirm(question)) return;
    void run(() => adminApi.transitionMedia(item.id, item.updated_at, archived ? 'restore' : 'archive'));
  }

  function remove() {
    if (!window.confirm('¿Eliminar este medio definitivamente? Se borran la foto publicada y el original. No se puede deshacer.')) return;
    void run(() => adminApi.deleteMedia(item.id, item.updated_at));
  }

  return (
    <div className={`bg-white border border-botanica-gris/15 rounded-xl overflow-hidden shadow-sm ${archived ? 'opacity-60' : ''}`}>
      <div className="relative">
        <Preview item={item} />
        <div className="absolute top-2 right-2 flex gap-1">
          <span aria-hidden="true" title="Arrastra para cambiar el orden"
            className="cursor-grab select-none rounded-md bg-white/90 px-2 py-1 text-botanica-grafito shadow-sm">⠿</span>
          <button type="button" disabled={locked || index === 0} onClick={() => onMove(index - 1)}
            aria-label="Mover antes" className="rounded-md bg-white/90 px-2 py-1 text-botanica-negro shadow-sm disabled:opacity-40">↑</button>
          <button type="button" disabled={locked || index === count - 1} onClick={() => onMove(index + 1)}
            aria-label="Mover después" className="rounded-md bg-white/90 px-2 py-1 text-botanica-negro shadow-sm disabled:opacity-40">↓</button>
        </div>
      </div>
      <div className="p-4 flex flex-col gap-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="tabular-nums font-medium text-botanica-negro">#{index + 1}</span>
          <Badge tone="neutral">{labels.role(item.media.role)}</Badge>
          {archived && <Badge tone="lavanda">Archivado</Badge>}
          {item.deletable && <Badge tone="neutral">Nunca publicado</Badge>}
        </div>
        <FormError message={error} />
        {mode === 'edit' && (
          <form onSubmit={save} className="flex flex-col gap-3">
            <TextArea id={`alt-${item.id}`} label="Descripción" value={alt} onChange={setAlt} disabled={busy}
              required={isImage} hint={ALT_HINT} />
            <Counter value={alt} />
            {roleOptions.length > 1 && (
              <Select id={`role-${item.id}`} label="Uso" value={role} onChange={(v) => setRole(v as MediaRole)} disabled={busy}
                options={roleOptions.map((r) => ({ value: r, label: labels.role(r) }))} />
            )}
            <div className="flex gap-2">
              <button type="submit" disabled={busy} className="btn-primary">Guardar</button>
              <button type="button" disabled={busy} onClick={cancel} className="btn-secondary">Cancelar</button>
            </div>
          </form>
        )}
        {mode === 'replace' && (
          <form onSubmit={replace} className="flex flex-col gap-3">
            <div>
              <label htmlFor={`replace-${item.id}`} className="block text-xs font-medium text-botanica-grafito mb-1">
                Archivo nuevo
              </label>
              <input id={`replace-${item.id}`} type="file" accept={ACCEPT} disabled={busy}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="block w-full text-sm text-botanica-grafito file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border file:border-botanica-gris/30 file:bg-white file:text-sm file:text-botanica-negro" />
              <p className="mt-1 text-xs text-botanica-gris">
                Conserva el uso y el orden. {item.deletable ? 'El archivo anterior se elimina.' : 'El archivo anterior se archiva.'}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {isImage && !file && (
                <button type="button" className="btn-secondary" disabled={busy} onClick={() => void editCurrent()}>Recortar o rotar la foto actual</button>
              )}
              {file && isPhoto(file) && (
                <button type="button" className="btn-secondary" disabled={busy} onClick={() => setEditorSource(file)}>Recortar o rotar</button>
              )}
            </div>
            {newPreview && <img src={newPreview} alt="" className="w-full aspect-[4/3] object-contain bg-botanica-hueso rounded-md" />}
            <TextArea id={`replace-alt-${item.id}`} label="Descripción" value={alt} onChange={setAlt} disabled={busy}
              required={isPhoto(file)} hint={ALT_HINT} />
            <Counter value={alt} />
            <div className="flex gap-2">
              <button type="submit" disabled={busy} className="btn-primary">{busy ? 'Reemplazando…' : 'Reemplazar'}</button>
              <button type="button" disabled={busy} onClick={cancel} className="btn-secondary">Cancelar</button>
            </div>
          </form>
        )}
        {mode === 'view' && (
          <>
            <p className="text-botanica-grafito">{item.media.alt_text ?? <span className="text-botanica-gris">Sin descripción</span>}</p>
            <div className="flex flex-wrap gap-2">
              {!archived && <button type="button" disabled={locked} onClick={() => setMode('edit')} className="btn-secondary">Editar</button>}
              {!archived && !ownerArchived && (
                <button type="button" disabled={locked} onClick={() => setMode('replace')} className="btn-secondary">Reemplazar o recortar</button>
              )}
              <button type="button" disabled={locked} onClick={toggleArchive} className="btn-secondary">{archived ? 'Restaurar' : 'Archivar'}</button>
              {item.deletable && (
                <button type="button" disabled={locked} onClick={remove}
                  className="btn-secondary text-red-700 border-red-200 hover:bg-red-50">Eliminar</button>
              )}
            </div>
          </>
        )}
      </div>
      {editorSource && (
        <PhotoEditor source={editorSource}
          onCancel={() => setEditorSource(null)}
          onDone={(blob) => { setFile(asFile(blob, `foto-${item.media.role}`)); setEditorSource(null); }} />
      )}
    </div>
  );
}
