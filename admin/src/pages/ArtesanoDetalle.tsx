import { Link, useParams } from 'react-router-dom';
import { adminApi } from '../api';
import { formatDate, joinList, labels } from '../format';
import { useState } from 'react';
import { useLoad } from '../hooks';
import { RecordActions } from '../RecordActions';
import { ErrorState, Field, Loading, PublicationBadge } from '../ui';

export default function ArtesanoDetalle() {
  const { id = '' } = useParams<{ id: string }>();
  const [revision, setRevision] = useState(0);
  const state = useLoad(`artisan:${id}:${revision}`, (signal) => adminApi.artisan(id, signal));

  return (
    <div className="max-w-4xl mx-auto pb-12">
      <Link to="/artesanos" className="inline-flex items-center text-sm font-medium text-botanica-grafito hover:text-botanica-negro mb-8">
        <svg className="w-4 h-4 mr-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
        Artesanos
      </Link>

      {state.status === 'loading' && <Loading label="Cargando artesano..." />}
      {state.status === 'error' && <div className="card-elevated"><ErrorState error={state.error} /></div>}
      {state.status === 'ready' && (() => {
        const a = state.data;
        const place = [a.locality, a.municipality, a.state, a.country].filter(Boolean).join(', ');
        return (
          <div className="flex flex-col gap-8">
            <RecordActions kind="artisans" id={a.id} version={a.updated_at} status={a.publication_status} onChanged={() => setRevision((r) => r + 1)} />
            <article className="bg-white rounded-xl border border-botanica-gris/15 overflow-hidden shadow-sm">
              <header className="bg-[#FCFBF9] border-b border-botanica-gris/15 p-8 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                  <h1 className="text-3xl font-serif text-botanica-negro tracking-tight mb-2">{a.full_name}</h1>
                  <p className="text-botanica-grafito">{a.artistic_name ?? 'Sin nombre artístico'}</p>
                </div>
                <PublicationBadge status={a.publication_status} />
              </header>
              <dl className="p-8 grid grid-cols-1 md:grid-cols-2 gap-8">
                <Field label="Origen">{place || '—'}</Field>
                <Field label="Técnicas">{joinList(a.techniques)}</Field>
                <Field label="Lenguas">{joinList(a.languages)} {a.languages?.length ? (a.languages_public ? '(públicas)' : '(privadas)') : ''}</Field>
                <Field label="Contacto (no público)">
                  {a.public_contact ? <code className="text-xs">{JSON.stringify(a.public_contact)}</code> : '—'}
                </Field>
                <Field label="Registrado">{formatDate(a.created_at)}</Field>
                <Field label="Actualizado">{formatDate(a.updated_at)}</Field>
                <div className="md:col-span-2"><Field label="Biografía">{a.biography ?? '—'}</Field></div>
                <div className="md:col-span-2"><Field label="Historia">{a.history ?? '—'}</Field></div>
                <Field label="Slug"><span className="font-mono text-sm">{a.slug}</span></Field>
                <Field label="ID"><span className="font-mono text-xs break-all">{a.id}</span></Field>
              </dl>
            </article>

            <section>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-2xl font-serif text-botanica-negro">Piezas ({a.pieces.length})</h2>
                {a.publication_status !== 'archived' && <Link to={`/piezas/nueva?artesano=${a.id}`} className="btn-secondary">Agregar pieza</Link>}
              </div>
              <div className="bg-white border border-botanica-gris/15 rounded-xl overflow-hidden shadow-sm">
                {a.pieces.length === 0 ? (
                  <p className="p-8 text-center text-botanica-gris">Este artesano todavía no tiene piezas.</p>
                ) : (
                  <ul className="divide-y divide-botanica-gris/10">
                    {a.pieces.map((p) => (
                      <li key={p.id} className="p-4 px-6 flex flex-wrap items-center gap-3 justify-between">
                        <Link to={`/piezas/${p.id}`} className="font-medium text-botanica-negro hover:text-botanica-jade">{p.name}</Link>
                        <span className="flex items-center gap-3 text-sm text-botanica-grafito">
                          <span className="font-mono text-xs">{p.public_code}</span>
                          {labels.availability(p.availability_status)}
                          <PublicationBadge status={p.publication_status} />
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>

            <section>
              <h2 className="text-2xl font-serif text-botanica-negro mb-4">Medios ({a.media.length})</h2>
              <p className="text-sm text-botanica-grafito">
                {a.media.length === 0
                  ? 'Sin fotos registradas. La subida de medios llega en la fase 4, con la capa de media del servidor.'
                  : a.media.map((m) => `${m.media.role} (${m.status})`).join(' · ')}
              </p>
            </section>
          </div>
        );
      })()}
    </div>
  );
}
