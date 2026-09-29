import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ApiError, adminApi } from '../api';
import type { ArtisanSummary, PieceDetail, PieceInput } from '../api';
import { blankToNull, labels, splitList, writeErrorMessage } from '../format';
import { FormError, Section, Select, TextArea, TextInput } from '../forms';
import { useLoad } from '../hooks';
import { ErrorState, Loading, PageHeader } from '../ui';

interface Values {
  artisan_id: string;
  name: string;
  public_code: string;
  slug: string;
  technique: string;
  materials: string;
  origin: string;
  creation_year: string;
  height_cm: string;
  width_cm: string;
  depth_cm: string;
  description: string;
  history: string;
  availability_status: string;
}

const AVAILABILITY = ['available', 'reserved', 'exhibited', 'archived'];

function fromDetail(p: PieceDetail): Values {
  const d = (p.dimensions ?? {}) as Record<string, unknown>;
  const num = (v: unknown) => (v === undefined || v === null ? '' : String(v));
  return {
    artisan_id: p.artisan.id, name: p.name, public_code: p.public_code, slug: p.slug,
    technique: p.technique ?? '', materials: (p.materials ?? []).join(', '), origin: p.origin ?? '',
    creation_year: p.creation_year ? String(p.creation_year) : '',
    height_cm: num(d.alto_cm), width_cm: num(d.ancho_cm), depth_cm: num(d.profundidad_cm),
    description: p.description ?? '', history: p.history ?? '', availability_status: p.availability_status,
  };
}

function dimensions(v: Values): Record<string, number> | null {
  const out: Record<string, number> = {};
  for (const [key, raw] of [['alto_cm', v.height_cm], ['ancho_cm', v.width_cm], ['profundidad_cm', v.depth_cm]] as const) {
    const n = Number(raw.replace(',', '.'));
    if (raw.trim() && Number.isFinite(n) && n > 0) out[key] = n;
  }
  return Object.keys(out).length ? out : null;
}

function toInput(v: Values, isDraft: boolean, isNew: boolean): PieceInput {
  const year = Number(v.creation_year);
  return {
    name: v.name.trim(),
    ...(isDraft ? { artisan_id: v.artisan_id } : {}),
    ...(isDraft && v.public_code.trim() ? { public_code: v.public_code.trim().toUpperCase() } : {}),
    ...(isDraft && v.slug.trim() ? { slug: v.slug.trim() } : {}),
    technique: blankToNull(v.technique),
    materials: splitList(v.materials),
    origin: blankToNull(v.origin),
    creation_year: v.creation_year.trim() && Number.isInteger(year) ? year : null,
    dimensions: dimensions(v),
    description: blankToNull(v.description),
    history: blankToNull(v.history),
    ...(isNew ? { availability_status: v.availability_status } : {}),
  };
}

function Form({ initial, existing, artisans }: { initial: Values; existing?: PieceDetail; artisans: ArtisanSummary[] }) {
  const navigate = useNavigate();
  const [values, setValues] = useState(initial);
  const [error, setError] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  const isDraft = !existing || existing.publication_status === 'draft';
  const set = (key: keyof Values) => (value: string) => setValues((v) => ({ ...v, [key]: value }));
  const fieldError = (key: string) => error?.fields[key] ?? (error?.field === key ? writeErrorMessage(error) : undefined);
  const options = [
    { value: '', label: 'Elige un artesano' },
    ...artisans.filter((a) => a.publication_status !== 'archived' || a.id === initial.artisan_id)
      .map((a) => ({ value: a.id, label: `${a.full_name} (${labels.publication(a.publication_status)})` })),
  ];

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!values.artisan_id) {
      setError(new ApiError('invalid', 422, { error: { details: [{ field: 'artisan_id', reason: 'Elige un artesano.' }] } }));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const saved = existing
        ? await adminApi.updatePiece(existing.id, existing.updated_at, toInput(values, isDraft, false))
        : await adminApi.createPiece(toInput(values, true, true));
      navigate(`/piezas/${saved.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('network', 0));
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="bg-white rounded-xl border border-botanica-gris/20 p-8 shadow-sm flex flex-col gap-8" noValidate>
      <FormError message={error ? writeErrorMessage(error) : null} />
      <Section title="Identidad">
        <Select id="artisan_id" label="Artesano" value={values.artisan_id} onChange={set('artisan_id')} options={options}
          disabled={!isDraft} required error={fieldError('artisan_id')} hint={isDraft ? undefined : 'Solo se puede cambiar en borrador.'} />
        <TextInput id="name" label="Nombre de la pieza" value={values.name} onChange={set('name')} required error={fieldError('name')} />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <TextInput id="public_code" label="Código público" value={values.public_code} onChange={set('public_code')} disabled={!isDraft}
            placeholder="Se genera: ANFC-XXXXXX" error={fieldError('public_code')}
            hint={isDraft ? 'Mayúsculas, números y guiones.' : 'Solo se puede cambiar en borrador.'} />
          <TextInput id="slug" label="Identificador para la URL" value={values.slug} onChange={set('slug')} disabled={!isDraft}
            placeholder="Se genera a partir del nombre" error={fieldError('slug')} />
        </div>
        {!existing && (
          <Select id="availability_status" label="Disponibilidad" value={values.availability_status} onChange={set('availability_status')}
            options={AVAILABILITY.map((a) => ({ value: a, label: labels.availability(a) }))} />
        )}
      </Section>
      <Section title="Detalles">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <TextInput id="technique" label="Técnica" value={values.technique} onChange={set('technique')} />
          <TextInput id="materials" label="Materiales" value={values.materials} onChange={set('materials')} hint="Separados por comas." error={fieldError('materials')} />
          <TextInput id="origin" label="Origen" value={values.origin} onChange={set('origin')} />
          <TextInput id="creation_year" label="Año de creación" type="number" value={values.creation_year} onChange={set('creation_year')} error={fieldError('creation_year')} />
        </div>
        <div className="grid grid-cols-3 gap-4">
          <TextInput id="height_cm" label="Alto (cm)" value={values.height_cm} onChange={set('height_cm')} />
          <TextInput id="width_cm" label="Ancho (cm)" value={values.width_cm} onChange={set('width_cm')} />
          <TextInput id="depth_cm" label="Profundidad (cm)" value={values.depth_cm} onChange={set('depth_cm')} />
        </div>
        <TextArea id="description" label="Descripción" value={values.description} onChange={set('description')} />
        <TextArea id="history" label="Historia" value={values.history} onChange={set('history')} />
      </Section>
      <div className="flex gap-4">
        <button type="submit" disabled={saving} className="btn-primary">{saving ? 'Guardando...' : existing ? 'Guardar cambios' : 'Crear pieza'}</button>
        <Link to={existing ? `/piezas/${existing.id}` : '/piezas'} className="btn-secondary">Cancelar</Link>
      </div>
      {!existing && <p className="text-xs text-botanica-gris">Se crea como borrador. Las fotos se agregan en una fase posterior.</p>}
    </form>
  );
}

export default function PiezaForm() {
  const { id } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  const preselected = params.get('artesano') ?? '';
  const state = useLoad(`piece-form:${id ?? 'new'}`, async (signal) => {
    const [artisans, piece] = await Promise.all([
      adminApi.artisans({}, signal),
      id ? adminApi.piece(id, signal) : Promise.resolve(null),
    ]);
    return { artisans: artisans.data, piece };
  });

  return (
    <div className="max-w-3xl mx-auto pb-12">
      <PageHeader title={id ? 'Editar pieza' : 'Nueva pieza'} />
      {state.status === 'loading' && <Loading label="Cargando..." />}
      {state.status === 'error' && <div className="card-elevated"><ErrorState error={state.error} /></div>}
      {state.status === 'ready' && (
        state.data.piece ? (
          <Form key={state.data.piece.updated_at} initial={fromDetail(state.data.piece)} existing={state.data.piece} artisans={state.data.artisans} />
        ) : (
          <Form
            initial={{ artisan_id: preselected, name: '', public_code: '', slug: '', technique: '', materials: '', origin: '',
              creation_year: '', height_cm: '', width_cm: '', depth_cm: '', description: '', history: '', availability_status: 'available' }}
            artisans={state.data.artisans}
          />
        )
      )}
    </div>
  );
}
