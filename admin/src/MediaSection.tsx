import { useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, MAX_UPLOAD_BYTES, adminApi, mediaUrl } from './api';
import type { AdminMedia, MediaRole } from './api';
import { labels, writeErrorMessage } from './format';
import { FormError, Select, TextArea, TextInput } from './forms';
import { Badge } from './ui';

// Roles each owner accepts (backend app/services/media.py ROLES).
const ROLES: Record<'artisans' | 'pieces', MediaRole[]> = {
  artisans: ['portrait', 'process', 'gallery'],
  pieces: ['hero', 'gallery', 'detail', 'process', 'model_3d'],
};

const ACCEPT = 'image/jpeg,image/png,image/webp,video/mp4,.glb,model/gltf-binary';

// Browsers give .glb files an empty type; the API sniffs the bytes anyway.
function contentTypeOf(file: File): string {
  if (file.type) return file.type;
  return file.name.toLowerCase().endsWith('.glb') ? 'model/gltf-binary' : 'application/octet-stream';
}

function isPhoto(file: File | null): boolean {
  return !!file && (file.type.startsWith('image/') || /\.(jpe?g|png|webp)$/i.test(file.name));
}

interface Props {
  kind: 'artisans' | 'pieces';
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
        El original se guarda aparte, en privado. Los archivos publicados nunca se reemplazan: archivar oculta un medio del sitio sin borrarlo.
      </p>
      {!ownerArchived && <UploadForm kind={kind} ownerId={ownerId} onUploaded={onChanged} />}
      {media.length === 0 ? (
        <p className="p-8 text-center text-botanica-gris bg-white border border-botanica-gris/15 rounded-xl">Sin medios todavía.</p>
      ) : (
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {media.map((m) => <MediaCard key={m.id} item={m} onChanged={onChanged} />)}
        </ul>
      )}
    </section>
  );
}

function UploadForm({ kind, ownerId, onUploaded }: { kind: 'artisans' | 'pieces'; ownerId: string; onUploaded: () => void }) {
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
    if (isPhoto(file) && !alt.trim()) {
      setError('Las fotos necesitan una descripción (texto alternativo).');
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
      setError(writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0)));
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
        <TextArea id={`media-alt-${ownerId}`} label="Descripción (texto alternativo)" value={alt} onChange={setAlt} disabled={busy}
          required={isPhoto(file)} hint="Qué se ve en la foto, para quien no puede verla. Obligatoria en fotos."
          placeholder="Máscara de madera tallada, vista de frente, con pintura roja y negra" />
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

function MediaCard({ item, onChanged }: { item: AdminMedia; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [alt, setAlt] = useState(item.media.alt_text ?? '');
  const [position, setPosition] = useState(String(item.media.position));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const archived = item.status === 'archived';

  async function run(call: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await call();
      setEditing(false);
      onChanged();
    } catch (e) {
      setError(writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0)));
    } finally {
      setBusy(false);
    }
  }

  function save(event: FormEvent) {
    event.preventDefault();
    const pos = Number.parseInt(position, 10);
    if (!Number.isInteger(pos) || pos < 0 || pos > 999) {
      setError('El orden es un número entre 0 y 999.');
      return;
    }
    void run(() => adminApi.updateMedia(item.id, item.updated_at, { alt_text: alt.trim() || null, position: pos }));
  }

  function toggleArchive() {
    const question = archived
      ? '¿Restaurar este medio? Volverá a verse en el sitio si el registro está publicado.'
      : '¿Archivar este medio? Dejará de verse en el sitio; el archivo no se borra.';
    if (!window.confirm(question)) return;
    void run(() => adminApi.transitionMedia(item.id, item.updated_at, archived ? 'restore' : 'archive'));
  }

  return (
    <li className={`bg-white border border-botanica-gris/15 rounded-xl overflow-hidden shadow-sm ${archived ? 'opacity-60' : ''}`}>
      <Preview item={item} />
      <div className="p-4 flex flex-col gap-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral">{labels.role(item.media.role)}</Badge>
          <span className="text-botanica-gris tabular-nums">Orden {item.media.position}</span>
          {archived && <Badge tone="lavanda">Archivado</Badge>}
        </div>
        <FormError message={error} />
        {editing ? (
          <form onSubmit={save} className="flex flex-col gap-3">
            <TextArea id={`alt-${item.id}`} label="Descripción" value={alt} onChange={setAlt} disabled={busy}
              required={item.media.type === 'image'} />
            <TextInput id={`pos-${item.id}`} label="Orden" type="number" value={position} onChange={setPosition} disabled={busy}
              hint="Menor número = aparece antes." />
            <div className="flex gap-2">
              <button type="submit" disabled={busy} className="btn-primary">Guardar</button>
              <button type="button" disabled={busy} onClick={() => setEditing(false)} className="btn-secondary">Cancelar</button>
            </div>
          </form>
        ) : (
          <>
            <p className="text-botanica-grafito">{item.media.alt_text ?? <span className="text-botanica-gris">Sin descripción</span>}</p>
            <div className="flex flex-wrap gap-2">
              {!archived && <button type="button" disabled={busy} onClick={() => setEditing(true)} className="btn-secondary">Editar</button>}
              <button type="button" disabled={busy} onClick={toggleArchive} className="btn-secondary">{archived ? 'Restaurar' : 'Archivar'}</button>
            </div>
          </>
        )}
      </div>
    </li>
  );
}
