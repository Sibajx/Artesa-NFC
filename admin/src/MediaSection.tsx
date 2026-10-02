import { useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, MAX_UPLOAD_BYTES, adminApi, mediaUrl } from './api';
import type { AdminMedia, MediaRole } from './api';
import { labels, writeErrorMessage } from './format';
import { FormError, Select, TextArea, TextInput } from './forms';
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
function contentTypeOf(file: File): string {
  if (file.type) return file.type;
  return file.name.toLowerCase().endsWith('.glb') ? 'model/gltf-binary' : 'application/octet-stream';
}

function isPhoto(file: File | null): boolean {
  return !!file && (file.type.startsWith('image/') || /\.(jpe?g|png|webp)$/i.test(file.name));
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
        Al subir una foto se le quitan los metadatos (incluida la ubicación GPS) y se reduce a 1600 px como máximo.
        El original se guarda aparte, en privado. Cada medio se puede <strong>editar</strong> (descripción, uso y orden)
        o <strong>reemplazar</strong> por otro archivo. Si nunca se vio en el sitio, también se puede <strong>eliminar</strong>{' '}
        por completo; si ya pudo verse, se <strong>archiva</strong>: deja de mostrarse sin borrarse.
      </p>
      {!ownerArchived && <UploadForm kind={kind} ownerId={ownerId} onUploaded={onChanged} />}
      {media.length === 0 ? (
        <p className="p-8 text-center text-botanica-gris bg-white border border-botanica-gris/15 rounded-xl">Sin medios todavía.</p>
      ) : (
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {media.map((m) => (
            <MediaCard key={m.id} kind={kind} ownerId={ownerId} item={m} ownerArchived={ownerArchived} onChanged={onChanged} />
          ))}
        </ul>
      )}
    </section>
  );
}

function UploadForm({ kind, ownerId, onUploaded }: { kind: Kind; ownerId: string; onUploaded: () => void }) {
  const roles = ROLES[kind];
  const [file, setFile] = useState<File | null>(null);
  const [role, setRole] = useState<MediaRole>(roles[0]);
  const [alt, setAlt] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inputKey, setInputKey] = useState(0);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError('Elige un archivo.');
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
    setBusy(true);
    setError(null);
    try {
      await adminApi.uploadMedia(kind, ownerId, file, contentTypeOf(file), role, alt.trim() || undefined);
      setFile(null);
      setAlt('');
      setInputKey((k) => k + 1);
      onUploaded();
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="bg-white border border-botanica-gris/15 rounded-xl p-6 mb-4 flex flex-col gap-4 shadow-sm">
      <h3 className="text-lg font-serif text-botanica-negro">Subir medio</h3>
      <FormError message={error} />
      <div>
        <label htmlFor={`media-file-${ownerId}`} className="block text-xs font-medium text-botanica-grafito mb-1">
          Archivo<span aria-hidden="true" className="text-botanica-jade"> *</span>
        </label>
        <input key={inputKey} id={`media-file-${ownerId}`} type="file" accept={ACCEPT} disabled={busy}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          aria-describedby={`media-file-${ownerId}-hint`}
          className="block w-full text-sm text-botanica-grafito file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border file:border-botanica-gris/30 file:bg-white file:text-sm file:text-botanica-negro" />
        <p id={`media-file-${ownerId}-hint`} className="mt-1 text-xs text-botanica-gris">
          Foto JPEG, PNG o WebP (hasta 25 MB) · video MP4 sin sonido ni ubicación (hasta 4 MB) · modelo 3D GLB (hasta 8 MB).
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Select id={`media-role-${ownerId}`} label="Uso" value={role} onChange={(v) => setRole(v as MediaRole)} disabled={busy}
          options={roles.map((r) => ({ value: r, label: labels.role(r) }))} />
        <div className="flex flex-col gap-3">
          <TextArea id={`media-alt-${ownerId}`} label="Descripción (texto alternativo)" value={alt} onChange={setAlt} disabled={busy}
            required={isPhoto(file)} hint={ALT_HINT}
            placeholder="Máscara de madera tallada, vista de frente, con pintura roja y negra" />
          <Counter value={alt} />
        </div>
      </div>
      <div><button type="submit" disabled={busy} className="btn-primary">{busy ? 'Subiendo…' : 'Subir'}</button></div>
    </form>
  );
}

function Preview({ item }: { item: AdminMedia }) {
  const { media } = item;
  const src = mediaUrl(media.url);
  if (media.type === 'image') {
    return <img src={src} alt={media.alt_text ?? ''} loading="lazy" className="w-full aspect-[4/3] object-cover bg-botanica-hueso" />;
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

function MediaCard({ kind, ownerId, item, ownerArchived, onChanged }: {
  kind: Kind; ownerId: string; item: AdminMedia; ownerArchived: boolean; onChanged: () => void;
}) {
  const [mode, setMode] = useState<Mode>('view');
  const [alt, setAlt] = useState(item.media.alt_text ?? '');
  const [position, setPosition] = useState(String(item.media.position));
  const [role, setRole] = useState<MediaRole>(item.media.role as MediaRole);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const archived = item.status === 'archived';
  const isImage = item.media.type === 'image';
  const roleOptions = ROLES[kind].filter((r) => ROLE_TYPES[r].includes(item.media.type));

  async function run(call: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await call();
      setMode('view');
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
    setPosition(String(item.media.position));
    setRole(item.media.role as MediaRole);
    setFile(null);
  }

  function save(event: FormEvent) {
    event.preventDefault();
    const pos = Number.parseInt(position, 10);
    if (!Number.isInteger(pos) || pos < 0 || pos > 999) {
      setError('El orden es un número entre 0 y 999.');
      return;
    }
    const invalidAlt = altError(alt, isImage);
    if (invalidAlt) {
      setError(invalidAlt);
      return;
    }
    const body: { alt_text: string | null; position: number; role?: MediaRole } = { alt_text: alt.trim() || null, position: pos };
    if (role !== item.media.role) body.role = role;
    void run(() => adminApi.updateMedia(item.id, item.updated_at, body));
  }

  // Replace = upload the new file with the same use, description and order,
  // then remove the old one: deleted when it was never public, archived
  // otherwise (a published file is never overwritten in place).
  function replace(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError('Elige el archivo nuevo.');
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
    <li className={`bg-white border border-botanica-gris/15 rounded-xl overflow-hidden shadow-sm ${archived ? 'opacity-60' : ''}`}>
      <Preview item={item} />
      <div className="p-4 flex flex-col gap-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral">{labels.role(item.media.role)}</Badge>
          <span className="text-botanica-gris tabular-nums">Orden {item.media.position}</span>
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
            <TextInput id={`pos-${item.id}`} label="Orden" type="number" value={position} onChange={setPosition} disabled={busy}
              hint="Menor número = aparece antes." />
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
                Archivo nuevo<span aria-hidden="true" className="text-botanica-jade"> *</span>
              </label>
              <input id={`replace-${item.id}`} type="file" accept={ACCEPT} disabled={busy}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="block w-full text-sm text-botanica-grafito file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border file:border-botanica-gris/30 file:bg-white file:text-sm file:text-botanica-negro" />
              <p className="mt-1 text-xs text-botanica-gris">
                Conserva el uso y el orden. {item.deletable ? 'El archivo anterior se elimina.' : 'El archivo anterior se archiva.'}
              </p>
            </div>
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
              {!archived && <button type="button" disabled={busy} onClick={() => setMode('edit')} className="btn-secondary">Editar</button>}
              {!archived && !ownerArchived && (
                <button type="button" disabled={busy} onClick={() => setMode('replace')} className="btn-secondary">Reemplazar archivo</button>
              )}
              <button type="button" disabled={busy} onClick={toggleArchive} className="btn-secondary">{archived ? 'Restaurar' : 'Archivar'}</button>
              {item.deletable && (
                <button type="button" disabled={busy} onClick={remove}
                  className="btn-secondary text-red-700 border-red-200 hover:bg-red-50">Eliminar</button>
              )}
            </div>
          </>
        )}
      </div>
    </li>
  );
}
