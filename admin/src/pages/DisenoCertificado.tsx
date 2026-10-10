import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError, adminApi } from '../api';
import type { ArtPlacement, Design, DesignParams } from '../api';
import { formatDateTime, writeErrorMessage } from '../format';
import { useConfirm, useToast } from '../feedback-context';
import { useLoad } from '../hooks';
import { TextArea, TextInput } from '../forms';
import { Badge, ErrorState, Loading } from '../ui';
import { whatsappUrl } from '../whatsapp';

// ADR-030 phase 5: design the piece's original certificate, get the artisan's
// approval and publish it. One server-side renderer draws every preview.

const STATUS: Record<Design['status'], { label: string; tone: 'neutral' | 'jade' | 'lavanda' }> = {
  draft: { label: 'Borrador', tone: 'neutral' },
  in_review: { label: 'En revisión con el artesano', tone: 'lavanda' },
  approved: { label: 'Aprobado', tone: 'jade' },
  published: { label: 'Publicado', tone: 'jade' },
  superseded: { label: 'Reemplazado', tone: 'neutral' },
};

const TEMPLATES: { value: DesignParams['template']; label: string; hint: string }[] = [
  { value: 'clasico', label: 'Clásico', hint: 'Papel, marco doble y sello' },
  { value: 'greca', label: 'Greca', hint: 'Marco de grecas con el color de la pieza' },
  { value: 'constelacion', label: 'Constelación', hint: 'Fondo oscuro con patrón generativo' },
];

const PLACEMENTS: { value: ArtPlacement; label: string; hint: string }[] = [
  { value: 'sello', label: 'En el sello', hint: 'Dentro del círculo, en lugar del sello generado' },
  { value: 'encabezado', label: 'Arriba', hint: 'Sobre el título, como un logotipo' },
  { value: 'fondo', label: 'De fondo', hint: 'Toda la hoja, tenue' },
];

const ART_TYPES = ['image/png', 'image/jpeg', 'image/webp'];

const MEDIA = [
  { value: 'en persona', label: 'En persona' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'llamada', label: 'Llamada' },
  { value: 'otro', label: 'Otro' },
];

const CONFLICTS: Record<string, string> = {
  open_design_exists: 'Ya hay un diseño en curso para esta pieza.',
  design_frozen: 'Un diseño aprobado o publicado ya no cambia. Crea una versión nueva.',
  invalid_design: 'Revisa los textos, los colores (de 3 a 5) y el arte.',
  unsupported_type: 'El arte tiene que ser una imagen PNG, JPEG o WebP.',
  unsupported_media_type: 'El arte tiene que ser una imagen PNG, JPEG o WebP.',
  image_too_large: 'La imagen es demasiado grande (más de 50 megapíxeles).',
  too_large: 'La imagen pesa más de 8 MB.',
  empty_file: 'El archivo está vacío.',
  invalid_approval: 'Indica quién aprobó, cómo, y qué revisaste (al menos 5 letras).',
  not_approved: 'Solo se publica un diseño aprobado.',
  invalid_transition: 'Ese paso ya no aplica. Recarga la página.',
};

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && CONFLICTS[e.code]) return CONFLICTS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

function piecePalette(theme: Record<string, unknown> | null): string[] {
  const value = (theme as { palette?: unknown } | null)?.palette;
  return Array.isArray(value) ? value.filter((c): c is string => typeof c === 'string') : [];
}

const svgSrc = (svg: string) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;

export default function DisenoCertificado() {
  const { id = '' } = useParams<{ id: string }>();
  const [revision, setRevision] = useState(0);
  const piece = useLoad(`piece:${id}`, (signal) => adminApi.piece(id, signal));
  const list = useLoad(`designs:${id}:${revision}`, (signal) => adminApi.designs(id, signal));
  const artisanId = piece.status === 'ready' ? piece.data.artisan.id : '';
  const artisan = useLoad(`artisan-contact:${artisanId}`, (signal) =>
    artisanId ? adminApi.artisan(artisanId, signal) : Promise.resolve(null));
  const contact = artisan.status === 'ready' && artisan.data
    ? { whatsapp: artisan.data.validation_whatsapp ?? null, name: artisan.data.validation_contact_name ?? null }
    : null;
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = () => setRevision((r) => r + 1);

  if (piece.status === 'loading' || list.status === 'loading') return <Loading label="Cargando diseño..." />;
  if (piece.status === 'error') return <div className="card-elevated"><ErrorState error={piece.error} /></div>;
  if (list.status === 'error') return <div className="card-elevated"><ErrorState error={list.error} /></div>;

  const versions = list.data.data;
  const current = versions.find((d) => d.id === selected) ?? versions[0];
  const canCreate = !versions.some((d) => ['draft', 'in_review', 'approved'].includes(d.status));

  async function create() {
    setError(null);
    try {
      const d = await adminApi.createDesign(id);
      setSelected(d.id);
      reload();
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <div className="max-w-6xl mx-auto pb-12 flex flex-col gap-6">
      <Link to={`/piezas/${id}`} className="inline-flex items-center text-sm font-medium text-botanica-grafito hover:text-botanica-negro">
        ← {piece.data.name}
      </Link>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-mono text-botanica-gris">{piece.data.public_code}</p>
          <h1 className="text-3xl font-serif text-botanica-negro">Certificado original</h1>
          <p className="text-sm text-botanica-grafito">Lo ve el dueño al abrir el certificado con su tarjeta.</p>
        </div>
        {canCreate && (
          <button type="button" className="btn-primary" onClick={() => void create()}>
            {versions.length ? `Nuevo diseño (versión ${versions[0].version + 1})` : 'Empezar diseño'}
          </button>
        )}
      </header>
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}

      {versions.length > 1 && (
        <nav aria-label="Versiones" className="flex flex-wrap gap-2">
          {versions.map((d) => (
            <button key={d.id} type="button" onClick={() => setSelected(d.id)}
              className={`rounded-full border px-3 py-1 text-xs ${d.id === current?.id ? 'border-botanica-jade bg-botanica-jade/10 text-botanica-negro' : 'border-botanica-gris/30 text-botanica-grafito'}`}>
              v{d.version} · {STATUS[d.status].label}
            </button>
          ))}
        </nav>
      )}

      {current ? (
        <Editor key={`${current.id}:${current.updated_at}`} designId={current.id} onChanged={reload} setError={setError}
          piecePalette={piecePalette(piece.data.visual_theme)} contact={contact} />
      ) : (
        <div className="card-elevated p-6 text-sm text-botanica-grafito">
          Esta pieza aún no tiene certificado original. Empieza un diseño: toma su nombre, su artesano y sus colores.
        </div>
      )}
    </div>
  );
}

type Contact = { whatsapp: string | null; name: string | null } | null;

function Editor({ designId, onChanged, setError, piecePalette, contact }: {
  designId: string; onChanged: () => void; setError: (m: string | null) => void; piecePalette: string[]; contact: Contact;
}) {
  const loaded = useLoad(`design:${designId}`, (signal) => adminApi.design(designId, signal));
  if (loaded.status === 'loading') return <Loading label="Cargando versión..." />;
  if (loaded.status === 'error') return <div className="card-elevated"><ErrorState error={loaded.error} /></div>;
  return <EditorForm design={loaded.data} onChanged={onChanged} setError={setError} piecePalette={piecePalette} contact={contact} />;
}

function EditorForm({ design, onChanged, setError, piecePalette, contact }: {
  design: Design; onChanged: () => void; setError: (m: string | null) => void; piecePalette: string[]; contact: Contact;
}) {
  const editable = design.status === 'draft' || design.status === 'in_review';
  const [params, setParams] = useState<DesignParams>(design.params);
  const [preview, setPreview] = useState('');
  const [editionText, setEditionText] = useState({
    number: design.params.edition ? String(design.params.edition.number) : '',
    total: design.params.edition ? String(design.params.edition.total) : '',
  });
  const [viewport, setViewport] = useState<'escritorio' | 'celular'>('escritorio');
  const [reviewUrl, setReviewUrl] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const [busy, setBusy] = useState(false);
  const confirm = useConfirm();
  const toast = useToast();
  const dirty = JSON.stringify(params) !== JSON.stringify(design.params);

  // Live preview of unsaved changes, debounced; the saved design keeps its own SVG.
  // Unsaved edits are previewed (debounced); with no edits the saved SVG is
  // shown, so going back to the saved design restores its picture. A late
  // answer for an older edit is dropped.
  useEffect(() => {
    if (!dirty) return;
    let current = true;
    const timer = window.setTimeout(() => {
      adminApi.previewDesign(params, design.version).then((r) => { if (current) setPreview(r.svg); }, () => undefined);
    }, 300);
    return () => { current = false; window.clearTimeout(timer); };
  }, [params, dirty, design.version]);
  const svg = dirty ? preview || design.svg || '' : design.svg ?? '';

  const set = <K extends keyof DesignParams>(key: K, value: DesignParams[K]) => setParams((p) => ({ ...p, [key]: value }));

  // Both numbers valid (1 ≤ X ≤ N ≤ 9999) → "Pieza X de N"; both empty → "Pieza única".
  function setEdition(next: { number: string; total: string }) {
    setEditionText(next);
    const number = Number(next.number);
    const total = Number(next.total);
    const valid = Number.isInteger(number) && Number.isInteger(total) && number >= 1 && number <= total && total <= 9999;
    set('edition', valid ? { number, total } : null);
  }
  const editionFilled = editionText.number !== '' || editionText.total !== '';
  const editionValid = !!params.edition;

  async function uploadArt(file: File | undefined) {
    if (!file) return;
    if (!ART_TYPES.includes(file.type)) {
      setError(CONFLICTS.unsupported_type);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const art = await adminApi.uploadArt(file, file.type);
      setParams((p) => ({ ...p, art: { id: art.id, placement: p.art?.placement ?? 'sello', opacity: p.art?.opacity ?? 0.15 } }));
      toast('Arte cargado. Guarda el diseño para conservarlo.');
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  }

  async function run(call: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError(null);
    try {
      await call();
      toast(done);
      onChanged();
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  }

  async function submit() {
    if (dirty) {
      setError('Guarda los cambios antes de enviarlo a revisión.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const sent = await adminApi.submitDesign(design.id, design.updated_at);
      setReviewUrl(sent.review_url);
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  }

  async function discard() {
    const approved = design.status === 'approved';
    const answer = await confirm({
      title: approved ? '¿Descartar este diseño aprobado?' : '¿Descartar este borrador?',
      body: approved
        ? 'Se borra esta versión para empezar de nuevo; la aprobación del artesano queda registrada en la auditoría. Si ya hay una versión publicada, sigue siendo la que ve el dueño.'
        : 'Se borra esta versión. Las versiones publicadas no se tocan.',
      confirmLabel: 'Descartar',
      tone: 'danger',
    });
    if (answer !== null) await run(() => adminApi.discardDesign(design.id, design.updated_at), 'Versión descartada');
  }

  async function publish() {
    const answer = await confirm({ title: `¿Publicar la versión ${design.version}?`, body: 'Desde ahora es la que ve el dueño al abrir su certificado original. La versión anterior queda como historial.', confirmLabel: 'Publicar' });
    if (answer !== null) await run(() => adminApi.publishDesign(design.id, design.updated_at), 'Diseño publicado');
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] items-start">
      <figure className="card-elevated p-3 lg:sticky lg:top-6">
        <div role="group" aria-label="Tamaño de la vista previa" className="mb-3 flex gap-2 text-sm">
          {(['escritorio', 'celular'] as const).map((v) => (
            <button key={v} type="button" aria-pressed={viewport === v} onClick={() => setViewport(v)}
              className={`rounded-full border px-3 py-1 ${viewport === v ? 'border-botanica-jade bg-botanica-jade/10 font-medium text-botanica-negro' : 'border-botanica-gris/30 text-botanica-grafito'}`}>
              {v === 'escritorio' ? 'Escritorio' : 'Celular'}
            </button>
          ))}
        </div>
        {svg ? (
          <img src={svgSrc(svg)} alt={`Vista previa del certificado de ${params.piece_name}`}
            className={viewport === 'celular'
              ? 'mx-auto w-full max-w-[300px] rounded-[2rem] border-[10px] border-botanica-negro bg-botanica-negro'
              : 'w-full rounded-lg'} />
        ) : <Loading label="Dibujando..." />}
        <figcaption className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-botanica-grafito">
          <span>Versión {design.version}</span>
          <Badge tone={STATUS[design.status].tone}>{STATUS[design.status].label}</Badge>
        </figcaption>
      </figure>

      <div className="flex flex-col gap-5 min-w-0">
        {design.change_request && design.status === 'draft' && (
          <p role="alert" className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-botanica-negro">
            <strong>El artesano pidió cambios:</strong> {design.change_request}
          </p>
        )}

        {reviewUrl && (
          <section className="card-elevated p-5 flex flex-col gap-3" aria-label="Enlace para el artesano">
            <h2 className="text-lg font-serif text-botanica-negro">Enlace para el artesano</h2>
            <p className="text-sm text-botanica-grafito">Se muestra una sola vez y caduca en 14 días. Mándalo por WhatsApp; el artesano lo abre y aprueba o pide cambios.</p>
            <input readOnly value={reviewUrl} className="w-full rounded-md border border-botanica-gris/30 px-3 py-2 font-mono text-xs" onFocus={(e) => e.target.select()} aria-label="Enlace de revisión" />
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-secondary" onClick={() => void navigator.clipboard?.writeText(reviewUrl).then(() => toast('Enlace copiado'), () => undefined)}>Copiar</button>
              <a className="btn-primary" target="_blank" rel="noreferrer"
                href={whatsappUrl(contact?.whatsapp, contact?.name
                  ? `Hola ${contact.name}. Somos de ArtesaNFC. Por favor enséñale a ${design.params.artisan_name} el diseño del certificado de su pieza "${design.params.piece_name}" y, si le gusta, toca "Sí, lo apruebo": ${reviewUrl}`
                  : `Hola, somos de ArtesaNFC. Este es el diseño del certificado de tu pieza "${design.params.piece_name}". Si te gusta, toca "Sí, lo apruebo": ${reviewUrl}`)}>
                {contact?.whatsapp ? `Mandar por WhatsApp${contact.name ? ` a ${contact.name}` : ''}` : 'Mandar por WhatsApp'}
              </a>
              <button type="button" className="btn-secondary" onClick={() => { setReviewUrl(null); onChanged(); }}>Listo</button>
            </div>
          </section>
        )}

        {editable ? (
          <section className="card-elevated p-5 flex flex-col gap-4" aria-label="Diseño">
            <fieldset className="flex flex-col gap-2">
              <legend className="text-xs font-medium text-botanica-grafito mb-1">Plantilla</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {TEMPLATES.map((t) => (
                  <label key={t.value} className={`cursor-pointer rounded-xl border p-3 text-sm ${params.template === t.value ? 'border-botanica-jade bg-botanica-jade/5' : 'border-botanica-gris/25'}`}>
                    <input type="radio" name="template" className="sr-only" checked={params.template === t.value} onChange={() => set('template', t.value)} />
                    <span className="block font-medium text-botanica-negro">{t.label}</span>
                    <span className="block text-xs text-botanica-gris">{t.hint}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            {params.template !== 'constelacion' && (
              <fieldset className="flex flex-wrap items-center gap-4 text-sm">
                <legend className="text-xs font-medium text-botanica-grafito mb-1">Fondo</legend>
                {(['claro', 'oscuro'] as const).map((v) => (
                  <label key={v} className="flex items-center gap-2"><input type="radio" name="variant" checked={params.variant === v} onChange={() => set('variant', v)} className="accent-botanica-jade" />{v === 'claro' ? 'Claro' : 'Oscuro'}</label>
                ))}
              </fieldset>
            )}
            <TextInput id="d-title" label="Título" value={params.title} onChange={(v) => set('title', v)} hint="Por ejemplo: Certificado original" />
            <div className="grid gap-4 sm:grid-cols-2">
              <TextInput id="d-piece" label="Nombre de la pieza" value={params.piece_name} onChange={(v) => set('piece_name', v)} />
              <TextInput id="d-artisan" label="Artesano" value={params.artisan_name} onChange={(v) => set('artisan_name', v)} />
            </div>
            <TextArea id="d-quote" label="Frase del artesano" value={params.quote} onChange={(v) => set('quote', v.slice(0, 240))}
              hint={`${params.quote.length}/240 · Opcional. Sus palabras sobre la pieza.`} />
            <fieldset className="flex flex-col gap-2">
              <legend className="text-xs font-medium text-botanica-grafito mb-1">Edición</legend>
              <div className="grid gap-4 sm:grid-cols-2">
                <TextInput id="d-ed-number" type="number" label="Pieza número" value={editionText.number}
                  onChange={(v) => setEdition({ ...editionText, number: v })} />
                <TextInput id="d-ed-total" type="number" label="De un total de" value={editionText.total}
                  onChange={(v) => setEdition({ ...editionText, total: v })} />
              </div>
              <p className={`text-xs ${editionFilled && !editionValid ? 'text-red-700' : 'text-botanica-grafito'}`}>
                {editionFilled && !editionValid
                  ? 'Escribe los dos números, del 1 al total (máximo 9999). Mientras no sean válidos se imprime "Pieza única".'
                  : 'Opcional. Para series: imprime "Pieza 3 de 10". Vacío imprime "Pieza única".'}
              </p>
            </fieldset>
            <fieldset className="flex flex-col gap-2">
              <legend className="text-xs font-medium text-botanica-grafito mb-1">Colores</legend>
              {piecePalette.length >= 3 && piecePalette.join() !== params.palette.join() && (
                <button type="button" className="self-start text-xs font-medium text-botanica-jade underline"
                  onClick={() => set('palette', piecePalette)}>
                  Usar los colores actuales de la pieza
                </button>
              )}
              <div className="flex flex-wrap gap-2">
                {params.palette.map((c, i) => (
                  <input key={i} type="color" value={c} aria-label={`Color ${i + 1}`}
                    onChange={(e) => set('palette', params.palette.map((x, j) => (j === i ? e.target.value : x)))}
                    className="h-11 w-11 cursor-pointer rounded-lg border border-botanica-gris/30 bg-transparent p-0.5" />
                ))}
              </div>
            </fieldset>
            <fieldset className="flex flex-col gap-3">
              <legend className="text-xs font-medium text-botanica-grafito mb-1">Arte propio</legend>
              <p className="text-xs text-botanica-gris">Un logotipo o dibujo exportado de Figma, Illustrator o Canva (PNG con fondo transparente queda mejor). Máximo 8 MB.</p>
              <div className="flex flex-wrap items-center gap-2">
                <label className={`btn-secondary cursor-pointer ${busy ? 'opacity-60 pointer-events-none' : ''}`}>
                  {params.art ? 'Cambiar imagen' : 'Subir imagen'}
                  <input type="file" accept={ART_TYPES.join(',')} className="sr-only"
                    onChange={(e) => { void uploadArt(e.target.files?.[0]); e.target.value = ''; }} />
                </label>
                {params.art && <button type="button" className="btn-secondary" disabled={busy} onClick={() => set('art', null)}>Quitar arte</button>}
              </div>
              {params.art && (
                <>
                  <div className="grid gap-2 sm:grid-cols-3">
                    {PLACEMENTS.map((pl) => (
                      <label key={pl.value} className={`cursor-pointer rounded-xl border p-3 text-sm ${params.art?.placement === pl.value ? 'border-botanica-jade bg-botanica-jade/5' : 'border-botanica-gris/25'}`}>
                        <input type="radio" name="art-placement" className="sr-only" checked={params.art?.placement === pl.value}
                          onChange={() => setParams((p) => (p.art ? { ...p, art: { ...p.art, placement: pl.value } } : p))} />
                        <span className="block font-medium text-botanica-negro">{pl.label}</span>
                        <span className="block text-xs text-botanica-gris">{pl.hint}</span>
                      </label>
                    ))}
                  </div>
                  {params.art.placement === 'fondo' && (
                    <label className="flex items-center gap-3 text-sm text-botanica-grafito">Intensidad
                      <input type="range" min={0.05} max={0.6} step={0.05} value={params.art.opacity} className="accent-botanica-jade"
                        onChange={(e) => { const opacity = Number(e.target.value); setParams((p) => (p.art ? { ...p, art: { ...p.art, opacity } } : p)); }} />
                      <span className="tabular-nums">{Math.round(params.art.opacity * 100)}%</span>
                    </label>
                  )}
                </>
              )}
            </fieldset>
            <div className="flex flex-wrap items-center gap-3">
              <button type="button" className="btn-secondary" onClick={() => set('seed', Math.floor(Math.random() * 2 ** 31))}>Otro patrón</button>
              <span className="text-xs text-botanica-gris">Cambia el sello{params.template === 'constelacion' ? ' y la constelación' : params.template === 'greca' ? ' y la greca' : ' y las esquinas'}.</span>
            </div>
            <div className="flex flex-wrap gap-2 border-t border-botanica-gris/15 pt-4">
              <button type="button" className="btn-primary" disabled={!dirty || busy}
                onClick={() => void run(() => adminApi.updateDesign(design.id, params, design.updated_at), 'Diseño guardado')}>Guardar</button>
              {dirty && <button type="button" className="btn-secondary" disabled={busy} onClick={() => setParams(design.params)}>Deshacer cambios</button>}
              <button type="button" className="btn-secondary" disabled={busy || dirty} onClick={() => void submit()}>
                {design.status === 'in_review' ? 'Generar otro enlace de revisión' : 'Enviar al artesano'}
              </button>
              <button type="button" className="btn-secondary" disabled={busy || dirty} onClick={() => setApproving(true)}>Registrar aprobación</button>
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => void discard()}>Descartar</button>
            </div>
            {design.status === 'in_review' && design.review_expires_at && (
              <p className="text-xs text-botanica-grafito">En revisión hasta {formatDateTime(design.review_expires_at)}. Si editas el diseño, el enlace deja de servir.</p>
            )}
          </section>
        ) : (
          <section className="card-elevated p-5 flex flex-col gap-3 text-sm" aria-label="Aprobación">
            <h2 className="text-lg font-serif text-botanica-negro">Aprobado por {design.approved_by_name}</h2>
            <dl className="grid gap-2 sm:grid-cols-2">
              <div><dt className="text-botanica-gris">Cuándo</dt><dd>{design.approved_at ? formatDateTime(design.approved_at) : ''}</dd></div>
              <div><dt className="text-botanica-gris">Cómo</dt><dd>{design.approval_medium}</dd></div>
              {design.approval_note && <div className="sm:col-span-2"><dt className="text-botanica-gris">Nota</dt><dd>{design.approval_note}</dd></div>}
              <div><dt className="text-botanica-gris">Registró</dt><dd className="break-all">{design.approval_recorded_by}</dd></div>
              {design.published_at && <div><dt className="text-botanica-gris">Publicado</dt><dd>{formatDateTime(design.published_at)}</dd></div>}
            </dl>
            {design.status === 'approved' && (
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-primary btn-publish" disabled={busy} onClick={() => void publish()}>Publicar</button>
                <button type="button" className="btn-secondary" disabled={busy} onClick={() => void discard()}>Descartar y empezar de nuevo</button>
              </div>
            )}
            {design.status !== 'approved' && <p className="text-xs text-botanica-gris">Esta versión ya no cambia. Para rediseñar, crea una versión nueva.</p>}
          </section>
        )}

        {approving && <ApprovalForm design={design} onDone={() => { setApproving(false); onChanged(); }} onCancel={() => setApproving(false)} setError={setError} />}
      </div>
    </div>
  );
}

function ApprovalForm({ design, onDone, onCancel, setError }: { design: Design; onDone: () => void; onCancel: () => void; setError: (m: string | null) => void }) {
  const [name, setName] = useState(design.params.artisan_name);
  const [medium, setMedium] = useState('en persona');
  const [note, setNote] = useState('');
  const toast = useToast();

  async function save() {
    setError(null);
    try {
      await adminApi.approveDesign(design.id, { name, medium, note }, design.updated_at);
      toast('Aprobación registrada');
      onDone();
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <section className="card-elevated p-5 flex flex-col gap-3" aria-label="Registrar aprobación">
      <h2 className="text-lg font-serif text-botanica-negro">Registrar la aprobación del artesano</h2>
      <p className="text-sm text-botanica-grafito">Para cuando lo aprobó sin el enlace: en persona, por WhatsApp o por teléfono.</p>
      <TextInput id="ap-name" label="Quién aprobó" value={name} onChange={setName} />
      <div>
        <label htmlFor="ap-medium" className="block text-xs font-medium text-botanica-grafito mb-1">Cómo</label>
        <select id="ap-medium" value={medium} onChange={(e) => setMedium(e.target.value)} className="w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white">
          {MEDIA.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </div>
      <TextArea id="ap-note" label="Qué revisaste" value={note} onChange={setNote} hint="Ej. lo vio impreso en el taller; mandó un audio aprobándolo." />
      <div className="flex gap-2">
        <button type="button" className="btn-primary" onClick={() => void save()}>Registrar aprobación</button>
        <button type="button" className="btn-secondary" onClick={onCancel}>Cancelar</button>
      </div>
    </section>
  );
}
