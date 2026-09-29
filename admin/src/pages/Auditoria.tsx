import { adminApi } from '../api';
import { formatDateTime } from '../format';
import { useLoad } from '../hooks';
import { Badge, ErrorState, Loading, PageHeader } from '../ui';

export default function Auditoria() {
  const state = useLoad('audit', (signal) => adminApi.auditEvents({ limit: '200' }, signal));

  return (
    <div className="max-w-6xl mx-auto pb-12">
      <PageHeader
        title="Auditoría"
        subtitle="Historial de cambios. Es de solo inserción: ningún evento se puede editar ni borrar."
      />
      <div className="bg-white rounded-xl border border-botanica-gris/20 shadow-sm overflow-hidden">
        {state.status === 'loading' && <Loading label="Cargando eventos..." />}
        {state.status === 'error' && <ErrorState error={state.error} />}
        {state.status === 'ready' && (state.data.data.length === 0 ? (
          <p className="p-12 text-center text-botanica-gris">
            Aún no hay eventos. Cada alta o edición de la fase 2 dejará aquí su registro.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-sm">
              <caption className="sr-only">Últimos {state.data.meta.total} eventos</caption>
              <thead>
                <tr className="table-header text-botanica-gris">
                  <th scope="col" className="py-4 px-6 font-medium">Fecha</th>
                  <th scope="col" className="py-4 px-6 font-medium">Usuario</th>
                  <th scope="col" className="py-4 px-6 font-medium">Acción</th>
                  <th scope="col" className="py-4 px-6 font-medium">Entidad</th>
                  <th scope="col" className="py-4 px-6 font-medium">Resultado</th>
                </tr>
              </thead>
              <tbody className="text-botanica-grafito">
                {state.data.data.map((e) => (
                  <tr key={e.id} className="border-b border-botanica-gris/10 last:border-0">
                    <td className="py-4 px-6 whitespace-nowrap">{formatDateTime(e.occurred_at)}</td>
                    <td className="py-4 px-6">{e.actor_email ?? e.actor_type}</td>
                    <td className="py-4 px-6 font-mono text-xs">{e.action}</td>
                    <td className="py-4 px-6 font-mono text-xs">{e.entity_type}/{e.entity_id.slice(0, 8)}</td>
                    <td className="py-4 px-6">
                      <Badge tone={e.result === 'success' ? 'jade' : 'lavanda'}>{e.result === 'success' ? 'Correcto' : 'Fallido'}</Badge>
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
