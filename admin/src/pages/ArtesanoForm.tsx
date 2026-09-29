import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, adminApi } from '../api';
import type { ArtisanDetail, ArtisanInput } from '../api';
import { blankToNull, splitList, writeErrorMessage } from '../format';
import { Checkbox, FormError, Section, TextArea, TextInput } from '../forms';
import { useLoad } from '../hooks';
import { ErrorState, Loading, PageHeader } from '../ui';

interface Values {
  full_name: string;
  artistic_name: string;
  slug: string;
  locality: string;
  municipality: string;
  state: string;
  country: string;
  languages: string;
  languages_public: boolean;
  techniques: string;
  biography: string;
  history: string;
  contact_phone: string;
  contact_email: string;
}

const EMPTY: Values = {
  full_name: '', artistic_name: '', slug: '', locality: '', municipality: '', state: 'Oaxaca', country: 'México',
  languages: '', languages_public: false, techniques: '', biography: '', history: '', contact_phone: '', contact_email: '',
};

function fromDetail(a: ArtisanDetail): Values {
  const contact = (a.public_contact ?? {}) as Record<string, unknown>;
  return {
    full_name: a.full_name, artistic_name: a.artistic_name ?? '', slug: a.slug,
    locality: a.locality ?? '', municipality: a.municipality ?? '', state: a.state ?? '', country: a.country ?? '',
    languages: (a.languages ?? []).join(', '), languages_public: a.languages_public,
    techniques: (a.techniques ?? []).join(', '), biography: a.biography ?? '', history: a.history ?? '',
    contact_phone: String(contact.telefono ?? ''), contact_email: String(contact.email ?? ''),
  };
}

function toInput(v: Values, isDraft: boolean): ArtisanInput {
  const contact: Record<string, string> = {};
  if (v.contact_phone.trim()) contact.telefono = v.contact_phone.trim();
  if (v.contact_email.trim()) contact.email = v.contact_email.trim();
  return {
    full_name: v.full_name.trim(),
    ...(isDraft && v.slug.trim() ? { slug: v.slug.trim() } : {}),
    artistic_name: blankToNull(v.artistic_name),
    locality: blankToNull(v.locality),
    municipality: blankToNull(v.municipality),
    state: blankToNull(v.state),
    country: blankToNull(v.country),
    languages: splitList(v.languages),
    languages_public: v.languages_public,
    techniques: splitList(v.techniques),
    biography: blankToNull(v.biography),
    history: blankToNull(v.history),
    public_contact: Object.keys(contact).length ? contact : null,
  };
}

function Form({ initial, existing }: { initial: Values; existing?: ArtisanDetail }) {
  const navigate = useNavigate();
  const [values, setValues] = useState(initial);
  const [error, setError] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  const isDraft = !existing || existing.publication_status === 'draft';
  const set = (key: keyof Values) => (value: string | boolean) => setValues((v) => ({ ...v, [key]: value }));
  const fieldError = (key: string) => error?.fields[key] ?? (error?.field === key ? writeErrorMessage(error) : undefined);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const saved = existing
        ? await adminApi.updateArtisan(existing.id, existing.updated_at, toInput(values, isDraft))
        : await adminApi.createArtisan(toInput(values, true));
      navigate(`/artesanos/${saved.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('network', 0));
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="bg-white rounded-xl border border-botanica-gris/20 p-8 shadow-sm flex flex-col gap-8" noValidate>
      <FormError message={error ? writeErrorMessage(error) : null} />
      <Section title="Identidad">
        <TextInput id="full_name" label="Nombre completo" value={values.full_name} onChange={set('full_name')} required error={fieldError('full_name')} />
        <TextInput id="artistic_name" label="Nombre artístico o de taller" value={values.artistic_name} onChange={set('artistic_name')} />
        <TextInput id="slug" label="Identificador para la URL" value={values.slug} onChange={set('slug')} disabled={!isDraft}
          placeholder="Se genera a partir del nombre" error={fieldError('slug')}
          hint={isDraft ? 'Minúsculas, números y guiones. Déjalo vacío para generarlo.' : 'Solo se puede cambiar en borrador.'} />
      </Section>
      <Section title="Origen">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <TextInput id="locality" label="Localidad" value={values.locality} onChange={set('locality')} />
          <TextInput id="municipality" label="Municipio" value={values.municipality} onChange={set('municipality')} />
          <TextInput id="state" label="Estado" value={values.state} onChange={set('state')} />
          <TextInput id="country" label="País" value={values.country} onChange={set('country')} />
        </div>
      </Section>
      <Section title="Oficio">
        <TextInput id="techniques" label="Técnicas" value={values.techniques} onChange={set('techniques')} hint="Separadas por comas." error={fieldError('techniques')} />
        <TextInput id="languages" label="Lenguas" value={values.languages} onChange={set('languages')} hint="Separadas por comas." error={fieldError('languages')} />
        <Checkbox id="languages_public" label="Mostrar las lenguas en el sitio público" checked={values.languages_public} onChange={set('languages_public')} />
        <TextArea id="biography" label="Biografía" value={values.biography} onChange={set('biography')} />
        <TextArea id="history" label="Historia" value={values.history} onChange={set('history')} />
      </Section>
      <Section title="Contacto (no se publica)">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <TextInput id="contact_phone" label="Teléfono" value={values.contact_phone} onChange={set('contact_phone')} />
          <TextInput id="contact_email" label="Email" value={values.contact_email} onChange={set('contact_email')} />
        </div>
      </Section>
      <div className="flex gap-4">
        <button type="submit" disabled={saving} className="btn-primary">{saving ? 'Guardando...' : existing ? 'Guardar cambios' : 'Crear artesano'}</button>
        <Link to={existing ? `/artesanos/${existing.id}` : '/artesanos'} className="btn-secondary">Cancelar</Link>
      </div>
      {!existing && <p className="text-xs text-botanica-gris">Se crea como borrador. Publícalo desde su ficha cuando esté listo.</p>}
    </form>
  );
}

export default function ArtesanoForm() {
  const { id } = useParams<{ id: string }>();
  const state = useLoad(`artisan-form:${id ?? 'new'}`, (signal) =>
    id ? adminApi.artisan(id, signal) : Promise.resolve(null),
  );

  return (
    <div className="max-w-3xl mx-auto pb-12">
      <PageHeader title={id ? 'Editar artesano' : 'Nuevo artesano'} />
      {state.status === 'loading' && <Loading label="Cargando..." />}
      {state.status === 'error' && <div className="card-elevated"><ErrorState error={state.error} /></div>}
      {state.status === 'ready' && (
        state.data ? <Form key={state.data.updated_at} initial={fromDetail(state.data)} existing={state.data} /> : <Form initial={EMPTY} />
      )}
    </div>
  );
}
