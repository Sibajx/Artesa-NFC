import { Link, useParams } from 'react-router-dom';
import { adminApi } from '../api';
import { formatDate, formatDateTime, joinList, labels } from '../format';
import { useState } from 'react';
import { useLoad } from '../hooks';
import { MediaSection } from '../MediaSection';
import { PaletteSection } from '../PaletteSection';
import { VentaSection } from '../VentaSection';
import { UbicacionSection } from '../UbicacionSection';
import { usePermissions } from '../roles-context';
import { PublishChecklist } from '../PublishChecklist';
import { RecordActions } from '../RecordActions';
import { Badge, ErrorState, Field, Loading, PublicationBadge } from '../ui';

export default function PiezaDetalle() {
  const permissions = usePermissions();
  const { id = '' } = useParams<{ id: string }>();
  const [revision, setRevision] = useState(0);
  const state = useLoad(`piece:${id}:${revision}`, (signal) => adminApi.piece(id, signal));

  return (
    <div className="max-w-4xl mx-auto pb-12">
      <Link to="/piezas" className="inline-flex items-center text-sm font-medium text-botanica-grafito hover:text-botanica-negro mb-8">
        <svg className="w-4 h-4 mr-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
        Piezas
      </Link>

      {state.status === 'loading' && <Loading label="Cargando pieza..." />}
      {state.status === 'error' && <div className="card-elevated"><ErrorState error={state.error} /></div>}
      {state.status === 'ready' && (() => {
        const p = state.data;
        return (
          <div className="flex flex-col gap-8">
            <RecordActions kind="pieces" id={p.id} version={p.updated_at} status={p.publication_status} trashedAt={p.trashed_at} purgeBlocker={p.purge_blocker} name={p.name} availability={p.availability_status} onChanged={() => setRevision((r) => r + 1)} />
            <PublishChecklist kind="pieces" record={p} onChanged={() => setRevision((r) => r + 1)} />
            <article className="bg-white rounded-xl border border-botanica-gris/15 overflow-hidden shadow-sm">
              <header className="bg-[#FCFBF9] border-b border-botanica-gris/15 p-8 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                  <h1 className="text-3xl font-serif text-botanica-negro tracking-tight mb-2">{p.name}</h1>
                  <p className="text-botanica-grafito font-mono text-sm tracking-wide">{p.public_code}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <PublicationBadge status={p.publication_status} />
                  <Badge tone={p.publicly_visible ? 'jade' : 'neutral'}>
                    {p.publicly_visible ? 'Visible en el sitio' : 'No visible en el sitio'}
                  </Badge>
                </div>
              </header>
              {!p.publicly_visible && p.publication_status === 'published' && (
                <p className="px-8 pt-6 text-sm text-botanica-grafito">
                  La pieza está publicada, pero su artesano no: el sitio público solo muestra piezas cuyo artesano también está publicado.
                </p>
              )}
              <dl className="p-8 grid grid-cols-1 md:grid-cols-2 gap-8">
                <Field label="Artesano">
                  <Link to={`/artesanos/${p.artisan.id}`} className="hover:text-botanica-jade">{p.artisan.full_name}</Link>{' '}
                  <span className="text-sm text-botanica-gris">({labels.publication(p.artisan.publication_status)})</span>
                </Field>
                <Field label="Disponibilidad">{labels.availability(p.availability_status)}</Field>
                <Field label="Técnica">{p.technique ?? '—'}</Field>
                <Field label="Materiales">{joinList(p.materials)}</Field>
                <Field label="Origen">{p.origin ?? '—'}</Field>
                <Field label="Año de creación">{p.creation_year ?? '—'}</Field>
                <div className="md:col-span-2"><Field label="Descripción">{p.description ?? '—'}</Field></div>
                <div className="md:col-span-2"><Field label="Historia">{p.history ?? '—'}</Field></div>
                <Field label="Registrada">{formatDate(p.created_at)}</Field>
                <Field label="Actualizada">{formatDate(p.updated_at)}</Field>
                <Field label="Slug"><span className="font-mono text-sm">{p.slug}</span></Field>
                <Field label="ID"><span className="font-mono text-xs break-all">{p.id}</span></Field>
              </dl>
            </article>

            {p.custody_visible ? (
              <>
            <section aria-labelledby="cert-heading">
              <h2 id="cert-heading" className="text-2xl font-serif text-botanica-negro mb-2">Certificados</h2>
              <p className="text-sm text-botanica-gris mb-4">Solo consulta. Emitir, reemplazar o revocar se hace en <Link to={`/certificacion/${p.id}`} className="underline text-botanica-jade">Certificación</Link>.</p>
              <div className="bg-white border border-botanica-gris/15 rounded-xl overflow-hidden shadow-sm">
                {p.certificates.length === 0 ? (
                  <p className="p-8 text-center text-botanica-gris">Sin certificado emitido.</p>
                ) : (
                  <table className="w-full text-left border-collapse text-sm">
                    <thead>
                      <tr className="table-header text-botanica-gris">
                        <th scope="col" className="py-3 px-6 font-medium">Versión</th>
                        <th scope="col" className="py-3 px-6 font-medium">Estado</th>
                        <th scope="col" className="py-3 px-6 font-medium">Emitido</th>
                        <th scope="col" className="py-3 px-6 font-medium">Revocado</th>
                      </tr>
                    </thead>
                    <tbody className="text-botanica-grafito">
                      {p.certificates.map((c) => (
                        <tr key={c.id} className="border-b border-botanica-gris/10 last:border-0">
                          <td className="py-3 px-6 tabular-nums">v{c.version}</td>
                          <td className="py-3 px-6"><Badge tone={c.status === 'active' ? 'jade' : c.status === 'revoked' ? 'lavanda' : 'neutral'}>{labels.certificate(c.status)}</Badge></td>
                          <td className="py-3 px-6">{formatDateTime(c.issued_at)}</td>
                          <td className="py-3 px-6">{c.revoked_at ? `${formatDateTime(c.revoked_at)}${c.revocation_reason ? ` · ${c.revocation_reason}` : ''}` : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </section>

            <section aria-labelledby="nfc-heading">
              <h2 id="nfc-heading" className="text-2xl font-serif text-botanica-negro mb-2">Etiquetas NFC</h2>
              <p className="text-sm text-botanica-gris mb-4">Solo consulta. Grabar y bloquear chips se hace en <Link to={`/certificacion/${p.id}`} className="underline text-botanica-jade">Certificación</Link>.</p>
              <div className="bg-white border border-botanica-gris/15 rounded-xl overflow-hidden shadow-sm">
                {p.nfc_tags.length === 0 ? (
                  <p className="p-8 text-center text-botanica-gris">Sin etiqueta asignada.</p>
                ) : (
                  <ul className="divide-y divide-botanica-gris/10 text-sm">
                    {p.nfc_tags.map((t) => (
                      <li key={t.id} className="p-4 px-6 flex flex-wrap items-center justify-between gap-3">
                        <span className="text-botanica-negro">{t.chip_model}</span>
                        <span className="flex items-center gap-3 text-botanica-grafito">
                          <span>Programada: {formatDate(t.programmed_at)}</span>
                          <span>Bloqueada: {formatDate(t.locked_at)}</span>
                          <Badge tone={t.status === 'locked' ? 'jade' : 'neutral'}>{labels.nfc(t.status)}</Badge>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>

              </>
            ) : (
              <section aria-label="Certificación" className="rounded-xl border border-botanica-gris/15 bg-white p-5 text-sm text-botanica-grafito">
                Los certificados y las etiquetas NFC solo los ven los Custodios (ADR-030).
              </section>
            )}

            <MediaSection kind="pieces" ownerId={p.id} media={p.media}
              ownerArchived={p.publication_status === 'archived'} onChanged={() => setRevision((r) => r + 1)} />

            <VentaSection key={`sale:${p.updated_at}`} piece={p} onChanged={() => setRevision((r) => r + 1)} />

            <UbicacionSection key={`location:${p.updated_at}`} piece={p} onChanged={() => setRevision((r) => r + 1)} />

            <PaletteSection key={p.updated_at} piece={p} onChanged={() => setRevision((r) => r + 1)} />

            {permissions.includes('design') && (
              <section className="card-elevated p-5 sm:p-6 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-xl font-serif text-botanica-negro">Certificado original</h2>
                  <p className="text-sm text-botanica-grafito">El diseño que ve el dueño con su tarjeta, aprobado por el artesano.</p>
                </div>
                <Link to={`/diseno/${p.id}`} className="btn-primary">Diseñar</Link>
              </section>
            )}
          </div>
        );
      })()}
    </div>
  );
}
