import { useState } from 'react';
import { ApiError, adminApi } from '../api';
import type { Account, AccountList } from '../api';
import { formatDateTime, writeErrorMessage } from '../format';
import { useConfirm, useToast } from '../feedback-context';
import { useLoad } from '../hooks';
import { ErrorState, Loading, PageHeader } from '../ui';

// P-026 G4: the owner adds, changes and removes Gestión accounts here.
// Accounts fixed in the server configuration (.env) are listed but locked.

const ROLES: { value: Account['role']; label: string; hint: string }[] = [
  { value: 'editor', label: 'Editor', hint: 'Artesanos, piezas, fotos, ventas' },
  { value: 'designer', label: 'Diseñador', hint: 'Lo anterior y el diseño de certificados' },
  { value: 'custodian', label: 'Custodio', hint: 'Todo: chips, tarjetas y certificados' },
  { value: 'hero', label: 'Hero', hint: 'Editor que también maneja los videos del hero del sitio' },
  { value: 'designer_hero', label: 'Diseñador y Hero', hint: 'Diseñador que también maneja el hero y las imágenes del sitio' },
];
const roleLabel = (r: string) => ROLES.find((x) => x.value === r)?.label ?? r;

// The permission checkboxes (backend app/core/permissions.py), in the order of the matrix.
const PERMISSIONS: Record<string, { label: string; hint: string }> = {
  view: { label: 'Ver', hint: 'Ver artesanos, piezas, resumen y auditoría' },
  edit: { label: 'Editar', hint: 'Crear y editar artesanos y piezas, fotos, paleta y papelera' },
  publish: { label: 'Publicar', hint: 'Publicar, despublicar y archivar; disponibilidad de la pieza' },
  authorization: { label: 'WhatsApp', hint: 'Autorización del artesano por WhatsApp' },
  sales: { label: 'Ventas', hint: 'Registrar y cancelar ventas' },
  logistics: { label: 'Logística', hint: 'Ubicación de la pieza y estados de envío' },
  nfc: { label: 'NFC', hint: 'Certificación: chips, tarjetas y código al dueño' },
  revocations: { label: 'Revocar', hint: 'Revocaciones, robos y bloqueo de tarjetas' },
  design: { label: 'Diseño', hint: 'Diseño de certificados' },
  hero: { label: 'Hero', hint: 'Hero por temporada e imágenes del sitio' },
  training: { label: 'Capacitar', hint: 'Capacitaciones (módulo todavía sin construir)' },
};

const CONFLICTS: Record<string, string> = {
  account_exists: 'Esa persona ya tiene acceso.',
  fixed_account: 'Esta cuenta está fija en la configuración del servidor; no se cambia desde aquí.',
  invalid_email: 'Escribe un correo válido.',
  owner_account: 'El dueño siempre tiene todos los permisos.',
  not_imported: 'Importa primero las cuentas fijas a Gestión.',
  view_required: 'Toda cuenta con acceso debe poder ver.',
};

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && CONFLICTS[e.code]) return CONFLICTS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

function SyncNotice({ list, onRetry }: { list: AccountList; onRetry: () => void }) {
  if (list.cloudflare === 'synced') {
    return <p className="rounded-xl border border-botanica-jade/30 bg-botanica-jade/5 p-3 text-sm text-botanica-negro">Cloudflare actualizado ✓</p>;
  }
  if (!list.sync_configured) {
    return (
      <p className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-botanica-negro">
        La conexión con Cloudflare no está configurada: después de agregar o quitar a alguien aquí, haz lo mismo en
        Cloudflare Zero Trust (política de Access de Gestión).
      </p>
    );
  }
  if (list.cloudflare !== 'unchanged') {
    return (
      <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">
        <span>El cambio se guardó en Gestión, pero Cloudflare no se actualizó: {list.cloudflare}.</span>
        <button type="button" className="btn-secondary" onClick={onRetry}>Reintentar</button>
      </div>
    );
  }
  return null;
}

type Run = (call: () => Promise<AccountList>, done: string) => Promise<boolean>;

// Persons x permissions. The owner always has everything; a fixed account (server
// config) has to be imported first so it can be edited. A role is a shortcut.
function Matrix({ list, busy, run }: { list: AccountList; busy: boolean; run: Run }) {
  const catalog = list.catalog;
  const hasFixed = list.data.some((a) => a.source === 'configuracion' && !a.owner && !a.imported);

  function toggle(a: Account, permission: string, on: boolean) {
    const next = catalog.filter((p) => (p === permission ? on : a.permissions.includes(p)));
    void run(() => adminApi.setAccountPermissions(a.email, next), 'Permisos actualizados');
  }

  return (
    <section className="card-elevated overflow-hidden" aria-labelledby="matrix-heading">
      <div className="p-5 sm:p-6 flex flex-col gap-2">
        <h2 id="matrix-heading" className="text-xl font-serif text-botanica-negro">Permisos</h2>
        <p className="text-sm text-botanica-grafito">
          Marca lo que cada persona puede hacer. El rol es un atajo: al elegirlo se marcan sus casillas, y después puedes
          ajustarlas una por una. Gestionar usuarios es solo del dueño.
        </p>
        {hasFixed && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-botanica-negro">
            <span>Algunas cuentas están fijas en la configuración del servidor. Impórtalas para poder editar sus permisos; no cambia el acceso de nadie.</span>
            <button type="button" className="btn-secondary" disabled={busy}
              onClick={() => void run(() => adminApi.importFixedAccounts(), 'Cuentas importadas')}>Importar a Gestión</button>
          </div>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-botanica-hueso/60 text-xs uppercase tracking-wide text-botanica-gris">
            <tr>
              <th className="px-4 py-3 text-left sticky left-0 bg-botanica-hueso">Persona</th>
              <th className="px-3 py-3 text-left">Atajo</th>
              {catalog.map((p) => <th key={p} scope="col" className="px-2 py-3 text-center font-medium" title={PERMISSIONS[p]?.hint}>{PERMISSIONS[p]?.label ?? p}</th>)}
            </tr>
          </thead>
          <tbody>
            {list.data.map((a) => {
              const editable = !a.owner && (a.source === 'gestion' || a.imported);
              return (
                <tr key={a.email} className="border-t border-botanica-gris/10">
                  <td className="px-4 py-3 whitespace-nowrap sticky left-0 bg-white">
                    {a.email}
                    {a.owner && <span className="ml-2 rounded-full bg-botanica-jade/10 px-2 py-0.5 text-xs text-botanica-jade">Dueño</span>}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap">
                    {a.source === 'gestion' && !a.owner ? (
                      <select value={a.custom ? 'custom' : a.role} disabled={busy} aria-label={`Atajo de ${a.email}`}
                        onChange={(e) => e.target.value !== 'custom' && void run(() => adminApi.changeAccountRole(a.email, e.target.value), 'Rol actualizado')}
                        className="px-2 py-1 border border-botanica-gris/30 rounded-md bg-white">
                        {a.custom && <option value="custom">A medida</option>}
                        {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                      </select>
                    ) : (
                      <span className="text-botanica-grafito">{a.owner ? 'Dueño' : a.custom ? 'A medida' : roleLabel(a.role)}{a.imported ? '' : a.owner ? '' : ' · fijo'}</span>
                    )}
                    {a.imported && a.custom && (
                      <button type="button" className="ml-2 text-xs text-botanica-jade underline" disabled={busy}
                        onClick={() => void run(() => adminApi.setAccountPermissions(a.email, null), 'Permisos restablecidos')}>Restablecer</button>
                    )}
                  </td>
                  {catalog.map((p) => {
                    const on = a.permissions.includes(p);
                    return (
                      <td key={p} className="px-2 py-3 text-center">
                        <input type="checkbox" checked={on} disabled={!editable || busy || (p === 'view' && on)}
                          aria-label={`${PERMISSIONS[p]?.label ?? p} para ${a.email}`}
                          onChange={(e) => toggle(a, p, e.target.checked)}
                          className="h-4 w-4 accent-botanica-jade disabled:opacity-50" />
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function Usuarios() {
  const loaded = useLoad('accounts', (signal) => adminApi.accounts(signal));
  // The latest answer from a change wins over the first load.
  const [changed, setList] = useState<AccountList | null>(null);
  const list = changed ?? (loaded.status === 'ready' ? loaded.data : null);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Account['role']>('editor');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = useConfirm();
  const toast = useToast();

  async function run(call: () => Promise<AccountList>, done: string) {
    setBusy(true);
    setError(null);
    try {
      setList(await call());
      toast(done);
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add() {
    if (await run(() => adminApi.addAccount(email.trim(), role, note.trim() || null), 'Acceso agregado')) {
      setEmail('');
      setNote('');
    }
  }

  async function remove(a: Account) {
    const answer = await confirm({ title: `¿Quitar el acceso de ${a.email}?`, body: 'Deja de poder entrar a Gestión de inmediato. Queda registrado en la auditoría.', confirmLabel: 'Quitar acceso', tone: 'danger' });
    if (answer !== null) await run(() => adminApi.removeAccount(a.email), 'Acceso quitado');
  }

  if (loaded.status === 'loading') return <Loading label="Cargando usuarios..." />;
  if (loaded.status === 'error') return <div className="card-elevated"><ErrorState error={loaded.error} /></div>;
  if (!list) return null;

  return (
    <div className="max-w-5xl mx-auto pb-12 flex flex-col gap-6">
      <PageHeader title="Usuarios" subtitle="Quién entra a Gestión y qué puede hacer. Solo tú ves esta página." />
      <SyncNotice list={list} onRetry={() => void run(() => adminApi.syncAccounts(), 'Sincronizado')} />
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      <section className="card-elevated p-5 sm:p-6 flex flex-col gap-4" aria-labelledby="add-heading">
        <h2 id="add-heading" className="text-xl font-serif text-botanica-negro">Dar acceso</h2>
        <form className="grid gap-3 md:grid-cols-[2fr_1fr_2fr_auto] items-end" onSubmit={(e) => { e.preventDefault(); void add(); }}>
          <label className="text-xs font-medium text-botanica-grafito">Correo (de Google)
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="nombre@gmail.com"
              className="mt-1 w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white" />
          </label>
          <label className="text-xs font-medium text-botanica-grafito">Rol
            <select value={role} onChange={(e) => setRole(e.target.value as Account['role'])}
              className="mt-1 w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white">
              {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </label>
          <label className="text-xs font-medium text-botanica-grafito">Nota (opcional)
            <input value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="Ej. fotografía de piezas"
              className="mt-1 w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white" />
          </label>
          <button type="submit" className="btn-primary" disabled={busy}>Agregar</button>
        </form>
        <p className="text-xs text-botanica-gris">{ROLES.map((r) => `${r.label}: ${r.hint}`).join(' · ')}</p>
      </section>

      <Matrix list={list} busy={busy} run={run} />

      <section className="card-elevated overflow-hidden" aria-label="Personas con acceso">
        <table className="w-full text-sm">
          <thead className="bg-botanica-hueso/60 text-left text-xs uppercase tracking-wide text-botanica-gris">
            <tr><th className="px-5 py-3">Correo</th><th className="px-5 py-3">Rol</th><th className="px-5 py-3 hidden md:table-cell">Alta</th><th className="px-5 py-3" /></tr>
          </thead>
          <tbody>
            {list.data.map((a) => (
              <tr key={a.email} className="border-t border-botanica-gris/10">
                <td className="px-5 py-3 break-all">
                  {a.email}
                  {a.owner && <span className="ml-2 rounded-full bg-botanica-jade/10 px-2 py-0.5 text-xs text-botanica-jade">Dueño</span>}
                  {a.note && <span className="block text-xs text-botanica-gris">{a.note}</span>}
                </td>
                <td className="px-5 py-3">
                  {a.source === 'gestion' ? (
                    <select value={a.role} disabled={busy} aria-label={`Rol de ${a.email}`}
                      onChange={(e) => void run(() => adminApi.changeAccountRole(a.email, e.target.value), 'Rol actualizado')}
                      className="px-2 py-1 border border-botanica-gris/30 rounded-md bg-white">
                      {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                    </select>
                  ) : (
                    <span title="Fijo en la configuración del servidor">{roleLabel(a.role)} · fijo</span>
                  )}
                </td>
                <td className="px-5 py-3 hidden md:table-cell text-botanica-gris">
                  {a.source === 'gestion' && a.added_at ? `${formatDateTime(a.added_at)} · ${a.added_by}` : 'Configuración del servidor'}
                </td>
                <td className="px-5 py-3 text-right">
                  {a.source === 'gestion' && (
                    <button type="button" className="btn-secondary text-red-700 border-red-200" disabled={busy} onClick={() => void remove(a)}>Quitar</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
