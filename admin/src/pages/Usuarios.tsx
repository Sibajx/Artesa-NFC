import { useState } from 'react';
import { ApiError, adminApi } from '../api';
import type { Account, AccountList, RoleProfile } from '../api';
import { formatDateTime, writeErrorMessage } from '../format';
import { useConfirm, useToast } from '../feedback-context';
import { useLoad } from '../hooks';
import { ErrorState, Loading, PageHeader } from '../ui';

// P-026 G4: the owner adds, changes and removes Gestión accounts here.
// Accounts fixed in the server configuration (.env) are listed but locked.

// The old fixed roles (server configuration) only label the accounts that are still derived from one.
const LEGACY_ROLES: Record<string, string> = {
  editor: 'Editor', designer: 'Diseñador', custodian: 'Custodio', hero: 'Hero', designer_hero: 'Diseñador y Hero',
};
const roleLabel = (r: string) => LEGACY_ROLES[r] ?? r;

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
  training: { label: 'Capacitar', hint: 'Manuales de capacitación por oficio; el dueño los revisa' },
  visits: { label: 'Visitas', hint: 'Bitácora de visitas a artesanos y galerías' },
  production: { label: 'Producción', hint: 'Tablero de producción: marcar pasos y subir fotos de cada pieza' },
};

const CONFLICTS: Record<string, string> = {
  account_exists: 'Esa persona ya tiene acceso.',
  fixed_account: 'Esta cuenta está fija en la configuración del servidor; no se cambia desde aquí.',
  invalid_email: 'Escribe un correo válido.',
  owner_account: 'El dueño siempre tiene todos los permisos.',
  not_imported: 'Importa primero las cuentas fijas a Gestión.',
  view_required: 'Toda cuenta o rol con acceso debe poder ver.',
  role_exists: 'Ya hay un rol con ese nombre.',
  invalid_name: 'El nombre del rol debe tener entre 2 y 40 caracteres.',
  too_many_roles: 'Ya hay demasiados roles; borra alguno que no uses.',
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
function Matrix({ list, busy, run, onRemove }: { list: AccountList; busy: boolean; run: Run; onRemove: (a: Account) => void }) {
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
          ajustarlas una por una; si las casillas coinciden con un rol, el atajo lo muestra solo. Los roles se editan
          más abajo. Gestionar usuarios es solo del dueño.
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
                    {a.note && <span className="block text-xs text-botanica-gris">{a.note}</span>}
                    <span className="block text-xs text-botanica-gris">
                      {a.source === 'gestion' && a.added_at ? `Alta ${formatDateTime(a.added_at)} · ${a.added_by}` : 'Configuración del servidor'}
                    </span>
                    {a.source === 'gestion' && !a.owner && (
                      <button type="button" className="mt-1 text-xs text-red-700 underline" disabled={busy} aria-label={`Quitar acceso de ${a.email}`} onClick={() => onRemove(a)}>Quitar acceso</button>
                    )}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap">
                    {editable ? (
                      <select value={a.profile ? list.profiles.find((r) => r.name === a.profile)?.slug ?? 'custom' : 'custom'}
                        disabled={busy} aria-label={`Atajo de ${a.email}`}
                        onChange={(e) => {
                          const picked = list.profiles.find((r) => r.slug === e.target.value);
                          if (picked) void run(() => adminApi.setAccountPermissions(a.email, picked.permissions), `Rol: ${picked.name}`);
                        }}
                        className="px-2 py-1 border border-botanica-gris/30 rounded-md bg-white">
                        <option value="custom" disabled>A medida</option>
                        {list.profiles.map((r) => <option key={r.slug} value={r.slug}>{r.name}</option>)}
                      </select>
                    ) : (
                      <span className="text-botanica-grafito">{a.owner ? 'Dueño' : `${a.profile ?? roleLabel(a.role)} · fijo`}</span>
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

// The role profiles: named sets of checkboxes. Editing one never changes anyone's access;
// it only changes what the shortcut copies the next time someone picks it.
function Roles({ list, busy, run, confirmDelete }: { list: AccountList; busy: boolean; run: Run; confirmDelete: (r: RoleProfile) => void }) {
  const catalog = list.catalog;
  const [name, setName] = useState('');

  function save(r: RoleProfile, patch: Partial<Pick<RoleProfile, 'name' | 'permissions'>>) {
    const next = { name: patch.name ?? r.name, description: r.description, permissions: patch.permissions ?? r.permissions };
    void run(() => adminApi.updateRole(r.slug, next), 'Rol actualizado');
  }

  function toggle(r: RoleProfile, permission: string, on: boolean) {
    save(r, { permissions: catalog.filter((p) => (p === permission ? on : r.permissions.includes(p))) });
  }

  async function create() {
    if (await run(() => adminApi.createRole({ name: name.trim(), description: null, permissions: ['view'] }), 'Rol creado')) setName('');
  }

  return (
    <section className="card-elevated overflow-hidden" aria-labelledby="roles-heading">
      <div className="p-5 sm:p-6 flex flex-col gap-2">
        <h2 id="roles-heading" className="text-xl font-serif text-botanica-negro">Roles</h2>
        <p className="text-sm text-botanica-grafito">
          Un rol es un conjunto de casillas con nombre. Al elegirlo en el atajo de una persona se le marcan sus casillas;
          si cambias un rol aquí, nadie pierde ni gana acceso hasta que se lo vuelvas a elegir.
        </p>
        <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); void create(); }}>
          <label className="text-xs font-medium text-botanica-grafito">Nuevo rol
            <input value={name} maxLength={40} minLength={2} required onChange={(e) => setName(e.target.value)} placeholder="Ej. Fotografía"
              className="mt-1 block w-56 px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white" />
          </label>
          <button type="submit" className="btn-secondary" disabled={busy}>Crear rol</button>
        </form>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-botanica-hueso/60 text-xs uppercase tracking-wide text-botanica-gris">
            <tr>
              <th className="px-4 py-3 text-left sticky left-0 bg-botanica-hueso">Rol</th>
              {catalog.map((p) => <th key={p} scope="col" className="px-2 py-3 text-center font-medium" title={PERMISSIONS[p]?.hint}>{PERMISSIONS[p]?.label ?? p}</th>)}
              <th className="px-3 py-3" />
            </tr>
          </thead>
          <tbody>
            {list.profiles.map((r) => (
              <tr key={r.slug} className="border-t border-botanica-gris/10">
                <td className="px-4 py-3 whitespace-nowrap sticky left-0 bg-white">
                  <input defaultValue={r.name} maxLength={40} disabled={busy} aria-label={`Nombre del rol ${r.name}`}
                    onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== r.name) save(r, { name: v }); else e.target.value = r.name; }}
                    className="w-44 px-2 py-1 border border-botanica-gris/30 rounded-md bg-white" />
                  {r.description && <span className="block text-xs text-botanica-gris max-w-44 whitespace-normal">{r.description}</span>}
                </td>
                {catalog.map((p) => {
                  const on = r.permissions.includes(p);
                  return (
                    <td key={p} className="px-2 py-3 text-center">
                      <input type="checkbox" checked={on} disabled={busy || (p === 'view' && on)}
                        aria-label={`${PERMISSIONS[p]?.label ?? p} en el rol ${r.name}`}
                        onChange={(e) => toggle(r, p, e.target.checked)} className="h-4 w-4 accent-botanica-jade disabled:opacity-50" />
                    </td>
                  );
                })}
                <td className="px-3 py-3">
                  <button type="button" className="text-xs text-red-700 underline" disabled={busy} aria-label={`Borrar el rol ${r.name}`} onClick={() => confirmDelete(r)}>Borrar</button>
                </td>
              </tr>
            ))}
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
  const [profile, setProfile] = useState('');
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
    // The account is created with the plainest legacy role and then gets the chosen profile's checkboxes.
    const chosen = list?.profiles.find((r) => r.slug === (profile || list.profiles[0]?.slug));
    const created = await run(() => adminApi.addAccount(email.trim(), 'editor', note.trim() || null), 'Acceso agregado');
    if (!created) return;
    if (chosen) await run(() => adminApi.setAccountPermissions(email.trim().toLowerCase(), chosen.permissions), `Acceso agregado · ${chosen.name}`);
    setEmail('');
    setNote('');
  }

  async function remove(a: Account) {
    const answer = await confirm({ title: `¿Quitar el acceso de ${a.email}?`, body: 'Deja de poder entrar a Gestión de inmediato. Queda registrado en la auditoría.', confirmLabel: 'Quitar acceso', tone: 'danger' });
    if (answer !== null) await run(() => adminApi.removeAccount(a.email), 'Acceso quitado');
  }

  async function removeRole(r: RoleProfile) {
    const answer = await confirm({ title: `¿Borrar el rol ${r.name}?`, body: 'Nadie pierde acceso: a quien lo tenga solo le dirá "A medida" en el atajo.', confirmLabel: 'Borrar rol', tone: 'danger' });
    if (answer !== null) await run(() => adminApi.deleteRole(r.slug), 'Rol borrado');
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
            <select value={profile || list.profiles[0]?.slug || ''} onChange={(e) => setProfile(e.target.value)}
              className="mt-1 w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white">
              {list.profiles.map((r) => <option key={r.slug} value={r.slug}>{r.name}</option>)}
            </select>
          </label>
          <label className="text-xs font-medium text-botanica-grafito">Nota (opcional)
            <input value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="Ej. fotografía de piezas"
              className="mt-1 w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white" />
          </label>
          <button type="submit" className="btn-primary" disabled={busy}>Agregar</button>
        </form>
        <p className="text-xs text-botanica-gris">{list.profiles.map((r) => `${r.name}: ${r.description ?? 'sin descripción'}`).join(' · ')}</p>
      </section>

      <Matrix list={list} busy={busy} run={run} onRemove={(a) => void remove(a)} />
      <Roles list={list} busy={busy} run={run} confirmDelete={(r) => void removeRole(r)} />
    </div>
  );
}
