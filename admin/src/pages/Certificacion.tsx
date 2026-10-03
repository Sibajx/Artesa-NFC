import { Link } from 'react-router-dom';
import { adminApi } from '../api';
import type { CustodyPiece } from '../api';
import { labels } from '../format';
import { useLoad } from '../hooks';
import { Badge, ErrorState, Loading, PageHeader, PublicationBadge } from '../ui';

// ADR-030 custody area, phase 1: the certification state of every piece.
// Only custodians reach it (the API answers 403 to anyone else). Generating
// tokens and card keys and writing tags arrive in the next phases.

function nextStep(p: CustodyPiece): string {
  if (p.publication_status !== 'published') return 'Publicar la pieza primero';
  if (p.certificate_status !== 'active') return 'Lista para certificar';
  if (!p.tag_status || p.tag_status === 'available') return 'Grabar la etiqueta';
  if (p.tag_status === 'programmed') return 'Certificada (etiqueta sin bloquear)';
  return 'Certificada y bloqueada';
}

export default function Certificacion() {
  const state = useLoad('custody:pieces', (signal) => adminApi.custodyPieces(signal));
  return (
    <div className="max-w-6xl mx-auto pb-12">
      <PageHeader title="Certificación" subtitle="Área de Custodios: certificados, claves y etiquetas NFC de cada pieza." />
      <p className="mb-6 rounded-xl border border-botanica-jade/25 bg-botanica-jade/5 p-4 text-sm text-botanica-grafito">
        Solo los Custodios ven esta sección. Abre una pieza para certificarla y grabar su chip desde un Android con Chrome
        (ADR-030). La clave de la tarjeta del comprador llega en la siguiente fase.
      </p>
      <div className="bg-white rounded-xl border border-botanica-gris/20 overflow-hidden shadow-sm">
        {state.status === 'loading' && <Loading label="Cargando certificación..." />}
        {state.status === 'error' && <ErrorState error={state.error} />}
        {state.status === 'ready' && (state.data.data.length === 0 ? (
          <p className="p-12 text-center text-botanica-gris">Todavía no hay piezas.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-sm">
              <caption className="sr-only">Estado de certificación por pieza</caption>
              <thead>
                <tr className="table-header text-botanica-gris">
                  <th scope="col" className="py-4 px-6 font-medium">Pieza</th>
                  <th scope="col" className="py-4 px-6 font-medium">Artesano</th>
                  <th scope="col" className="py-4 px-6 font-medium">Publicación</th>
                  <th scope="col" className="py-4 px-6 font-medium">Certificado</th>
                  <th scope="col" className="py-4 px-6 font-medium">Etiqueta NFC</th>
                  <th scope="col" className="py-4 px-6 font-medium">Siguiente paso</th>
                </tr>
              </thead>
              <tbody className="text-botanica-grafito">
                {state.data.data.map((p) => (
                  <tr key={p.id} className="border-b border-botanica-gris/10 last:border-0 table-row-hover">
                    <td className="py-4 px-6">
                      <Link to={`/piezas/${p.id}`} className="font-medium text-botanica-negro hover:text-botanica-jade">{p.name}</Link>
                      <div className="font-mono text-xs text-botanica-gris">{p.public_code}</div>
                    </td>
                    <td className="py-4 px-6">{p.artisan_name}</td>
                    <td className="py-4 px-6"><PublicationBadge status={p.publication_status} /></td>
                    <td className="py-4 px-6">
                      {p.certificate_status
                        ? <Badge tone={p.certificate_status === 'active' ? 'jade' : 'neutral'}>{labels.certificate(p.certificate_status)} · v{p.certificate_version}</Badge>
                        : <span className="text-botanica-gris">Sin emitir</span>}
                    </td>
                    <td className="py-4 px-6">
                      {p.tag_status
                        ? <Badge tone={p.tag_status === 'locked' ? 'jade' : 'neutral'}>{labels.nfc(p.tag_status)}</Badge>
                        : <span className="text-botanica-gris">Sin etiqueta</span>}
                    </td>
                    <td className="py-4 px-6">
                      <Link to={`/certificacion/${p.id}`} className={p.ready_to_certify ? 'btn-primary !py-1.5 !px-3 text-xs' : 'text-botanica-grafito underline hover:text-botanica-jade'}>
                        {p.ready_to_certify ? 'Certificar' : nextStep(p)}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
