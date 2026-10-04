import { useState } from 'react';
import { Link } from 'react-router-dom';
import { adminApi } from '../api';
import { AUDIT_CATEGORIES, actionLabel, actorLabel, entityLink } from '../audit-labels';
import { formatDateTime } from '../format';
import { useLoad } from '../hooks';
import { Badge, ErrorState, Loading, PageHeader } from '../ui';

// P-026 G7: filters (category, person, dates) and actions in plain Spanish.
// The trail itself stays append-only: nothing here edits or deletes.

interface Filters {
  action_prefix: string;
  actor_email: string;
  since: string;
  until: string;
}

const EMPTY: Filters = { action_prefix: '', actor_email: '', since: '', until: '' };
const INPUT = 'mt-1 w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white';

export default function Auditoria() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const key = `audit:${JSON.stringify(applied)}`;
  const state = useLoad(key, (signal) => adminApi.auditEvents({
    limit: '200',
    action_prefix: applied.action_prefix || undefined,
    actor_email: applied.actor_email.trim() || undefined,
    since: applied.since || undefined,
    until: applied.until || undefined,
  }, signal));
  const set = (k: keyof Filters) => (e: { target: { value: string } }) => setDraft((d) => ({ ...d, [k]: e.target.value }));
  const filtered = JSON.stringify(applied) !== JSON.stringify(EMPTY);

  return (
    <div className="max-w-6xl mx-auto pb-12">
      <PageHeader
        title="Auditoría"
        subtitle="Historial de cambios. Es de solo inserción: ningún evento se puede editar ni borrar."
      />
      <form className="mb-6 grid gap-3 md:grid-cols-[1.4fr_1.6fr_1fr_1fr_auto] items-end bg-white rounded-xl border border-botanica-gris/20 p-4 shadow-sm"
        onSubmit={(e) => { e.preventDefault(); setApplied(draft); }}>
        <label className="text-xs font-medium text-botanica-grafito">Tipo
          <select value={draft.action_prefix} onChange={set('action_prefix')} className={INPUT}>
            {AUDIT_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </label>
        <label className="text-xs font-medium text-botanica-grafito">Persona (correo)
          <input type="email" value={draft.actor_email} onChange={set('actor_email')} placeholder="nombre@gmail.com" className={INPUT} />
        </label>
        <label className="text-xs font-medium text-botanica-grafito">Desde
          <input type="date" value={draft.since} onChange={set('since')} className={INPUT} />
        </label>
        <label className="text-xs font-medium text-botanica-grafito">Hasta
          <input type="date" value={draft.until} onChange={set('until')} className={INPUT} />
        </label>
        <div className="flex gap-2">
          <button type="submit" className="btn-primary">Filtrar</button>
          {filtered && <button type="button" className="btn-secondary" onClick={() => { setDraft(EMPTY); setApplied(EMPTY); }}>Limpiar</button>}
        </div>
      </form>

      <div className="bg-white rounded-xl border border-botanica-gris/20 shadow-sm overflow-hidden">
        {state.status === 'loading' && <Loading label="Cargando eventos..." />}
        {state.status === 'error' && <ErrorState error={state.error} />}
        {state.status === 'ready' && (state.data.data.length === 0 ? (
          <p className="p-12 text-center text-botanica-gris">
            {filtered ? 'Ningún evento coincide con el filtro.' : 'Aún no hay eventos.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-sm">
              <caption className="sr-only">Últimos {state.data.meta.total} eventos</caption>
              <thead>
                <tr className="table-header text-botanica-gris">
                  <th scope="col" className="py-4 px-6 font-medium">Fecha</th>
                  <th scope="col" className="py-4 px-6 font-medium">Quién</th>
                  <th scope="col" className="py-4 px-6 font-medium">Qué hizo</th>
                  <th scope="col" className="py-4 px-6 font-medium">Registro</th>
                  <th scope="col" className="py-4 px-6 font-medium">Resultado</th>
                </tr>
              </thead>
              <tbody className="text-botanica-grafito">
                {state.data.data.map((e) => {
                  const to = entityLink(e.entity_type, e.entity_id);
                  return (
                    <tr key={e.id} className="border-b border-botanica-gris/10 last:border-0">
                      <td className="py-4 px-6 whitespace-nowrap">{formatDateTime(e.occurred_at)}</td>
                      <td className="py-4 px-6 break-all">{actorLabel(e.actor_email, e.actor_type)}</td>
                      <td className="py-4 px-6">
                        {actionLabel(e.action)}
                        <span className="block font-mono text-[11px] text-botanica-gris">{e.action}</span>
                      </td>
                      <td className="py-4 px-6">
                        {to
                          ? <Link to={to} className="underline decoration-botanica-gris/40 hover:text-botanica-jade">Ver {e.entity_type === 'piece' ? 'pieza' : 'artesano'}</Link>
                          : <span className="font-mono text-xs">{e.entity_type}</span>}
                      </td>
                      <td className="py-4 px-6">
                        <Badge tone={e.result === 'success' ? 'jade' : 'lavanda'}>{e.result === 'success' ? 'Correcto' : 'Fallido'}</Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
