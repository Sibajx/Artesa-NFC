import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import type { ReactElement } from 'react';
import { adminApi, LOGOUT_URL } from './api';
import { useLoad } from './hooks';
import { ErrorState, Loading, ThemeToggle } from './ui';
import Dashboard from './pages/Dashboard';
import Artesanos from './pages/Artesanos';
import ArtesanoDetalle from './pages/ArtesanoDetalle';
import Piezas from './pages/Piezas';
import PiezaDetalle from './pages/PiezaDetalle';
import Auditoria from './pages/Auditoria';
import Apartado from './pages/Apartado';
import Certificacion from './pages/Certificacion';
import CertificarPieza from './pages/CertificarPieza';
import DisenoCertificado from './pages/DisenoCertificado';
import Usuarios from './pages/Usuarios';
import Inventario from './pages/Inventario';
import Insumos from './pages/Insumos';
import Produccion from './pages/Produccion';
import Visitas from './pages/Visitas';
import Hero from './pages/Hero';
import { PermissionsContext, RolesContext } from './roles-context';
import ArtesanoForm from './pages/ArtesanoForm';
import PiezaForm from './pages/PiezaForm';

const icons: Record<string, ReactElement> = {
  Resumen: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
      <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
    </svg>
  ),
  Artesanos: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
      <circle cx="9" cy="7" r="4"/>
      <path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
    </svg>
  ),
  Piezas: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polygon points="12 2 2 7 12 12 22 7 12 2"/>
      <polyline points="2 17 12 22 22 17"/>
      <polyline points="2 12 12 17 22 12"/>
    </svg>
  ),
  Inventario: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 8l-9-5-9 5v8l9 5 9-5z"/>
      <path d="M3 8l9 5 9-5"/>
      <path d="M12 13v8"/>
    </svg>
  ),
  Producción: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1 3h15v13H1z"/>
      <path d="M16 8h4l3 3v5h-7z"/>
      <circle cx="5.5" cy="18.5" r="2"/><circle cx="18.5" cy="18.5" r="2"/>
    </svg>
  ),
  Visitas: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/>
      <circle cx="12" cy="10" r="2.5"/>
    </svg>
  ),
  Insumos: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 7h16"/>
      <path d="M6 7l1 13h10l1-13"/>
      <path d="M9 7V4h6v3"/>
    </svg>
  ),
  Certificación: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2l7 4v6c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6z"/>
      <path d="M9 12l2 2 4-4"/>
    </svg>
  ),
  Archivados: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="3" width="20" height="5" rx="1"/>
      <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/>
      <line x1="10" y1="12" x2="14" y2="12"/>
    </svg>
  ),
  Papelera: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="3 6 5 6 21 6"/>
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
      <path d="M10 11v6"/><path d="M14 11v6"/>
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
    </svg>
  ),
  Usuarios: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
      <circle cx="9" cy="7" r="4"/>
      <path d="M22 21v-2a4 4 0 0 0-3-3.87"/>
      <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
    </svg>
  ),
  Hero: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="4" width="20" height="14" rx="2"/>
      <polygon points="10 8.5 15 11 10 13.5 10 8.5"/>
      <line x1="7" y1="21" x2="17" y2="21"/>
    </svg>
  ),
  Auditoría: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
      <line x1="16" y1="13" x2="8" y2="13"/>
      <line x1="16" y1="17" x2="8" y2="17"/>
    </svg>
  ),
};

// `any` = the account needs at least one of these permissions; `role` = a role (the owner's page).
const menuItems: { name: string; path: string; any?: string[]; role?: string }[] = [
  { name: 'Resumen', path: '/resumen' },
  { name: 'Artesanos', path: '/artesanos' },
  { name: 'Piezas', path: '/piezas' },
  { name: 'Inventario', path: '/inventario' },
  { name: 'Producción', path: '/produccion' },
  { name: 'Insumos', path: '/insumos' },
  { name: 'Visitas', path: '/visitas', any: ['visits'] },
  { name: 'Certificación', path: '/certificacion', any: ['nfc', 'revocations'] },
  { name: 'Archivados', path: '/archivados' },
  { name: 'Papelera', path: '/papelera' },
  { name: 'Hero', path: '/hero', any: ['hero'] },
  { name: 'Auditoría', path: '/auditoria' },
  { name: 'Usuarios', path: '/usuarios', role: 'owner' },
];

export default function App() {
  // Access already authenticated the browser; /me confirms the API accepts
  // this identity (allowlist) before anything else is shown.
  const me = useLoad('me', (signal) => adminApi.me(signal));

  if (me.status === 'loading') return <Loading label="Verificando acceso..." />;
  if (me.status === 'error') {
    return (
      <div className="min-h-screen flex items-center justify-center p-6">
        <div className="card-elevated max-w-md w-full">
          <ErrorState error={me.error} />
        </div>
      </div>
    );
  }

  const email = me.data.email;
  const roles = me.data.roles ?? ['editor'];
  const permissions = me.data.permissions ?? [];
  const can = (...wanted: string[]) => wanted.some((p) => permissions.includes(p));
  const visibleItems = menuItems.filter((item) => (!item.role || roles.includes(item.role)) && (!item.any || can(...item.any)));

  return (
    <div className="min-h-screen flex flex-col md:flex-row bg-botanica-hueso">
      <aside className="sidebar w-full md:w-60 border-r border-botanica-gris/20 flex flex-col shrink-0">
        <div className="px-6 pt-7 pb-6 border-b border-botanica-gris/20">
          <div className="flex items-center gap-2.5 mb-1">
            <div className="side-logo w-7 h-7 rounded-md bg-botanica-jade/10 border border-botanica-jade/20 flex items-center justify-center shrink-0">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="text-botanica-jade" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M6 8.32a7.43 7.43 0 0 1 0 7.36"/>
                <path d="M9.46 6.21a11.76 11.76 0 0 1 0 11.58"/>
                <path d="M12.91 4.1a15.91 15.91 0 0 1 .01 15.8"/>
              </svg>
            </div>
            <p className="text-base font-serif text-botanica-negro">ArtesaNFC</p>
          </div>
          <p className="text-xs text-botanica-gris pl-[38px]">Consola de gestión</p>
        </div>

        <nav aria-label="Principal" className="side-nav flex-1 px-3 py-4 flex flex-col gap-0.5">
          {visibleItems.map((item) => (
            <NavLink
              key={item.name}
              to={item.path}
              className={({ isActive }) =>
                `side-link flex items-center gap-3 px-3 py-2 rounded-lg text-sm ${
                  isActive
                    ? 'bg-botanica-jade/10 text-botanica-jade font-medium'
                    : 'text-botanica-grafito hover:bg-botanica-hueso hover:text-botanica-negro'
                }`
              }
            >
              <span className="side-link__icon shrink-0 opacity-70">{icons[item.name]}</span>
              {item.name}
            </NavLink>
          ))}
        </nav>

        <div className="px-4 py-4 border-t border-botanica-gris/20 flex flex-col gap-3">
          <ThemeToggle />
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-botanica-jade/15 border border-botanica-jade/20 flex items-center justify-center shrink-0">
              <span className="text-xs font-semibold text-botanica-jade" aria-hidden="true">{email.charAt(0).toUpperCase()}</span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-botanica-negro truncate" title={email}>{email}</p>
              <p className="text-xs text-botanica-gris">
                {roles.includes('owner') ? 'Dueño' : roles.includes('custodian') ? 'Custodio' : roles.includes('designer') && roles.includes('hero') ? 'Diseñador y Hero' : roles.includes('designer') ? 'Diseñador' : roles.includes('hero') ? 'Hero y contenido' : 'Editor de contenido'}
              </p>
            </div>
            <a
              href={LOGOUT_URL}
              title="Cerrar sesión"
              aria-label="Cerrar sesión"
              className="shrink-0 text-botanica-gris hover:text-botanica-negro transition-colors"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
                <polyline points="16 17 21 12 16 7"/>
                <line x1="21" y1="12" x2="9" y2="12"/>
              </svg>
            </a>
          </div>
        </div>
      </aside>

      <main className="flex-1 p-6 md:p-10 overflow-auto">
        <RolesContext.Provider value={roles}>
        <PermissionsContext.Provider value={permissions}>
        <Routes>
          <Route path="/resumen" element={<Dashboard />} />
          <Route path="/artesanos/nuevo" element={<ArtesanoForm />} />
          <Route path="/artesanos/:id/editar" element={<ArtesanoForm />} />
          <Route path="/artesanos/:id" element={<ArtesanoDetalle />} />
          <Route path="/artesanos" element={<Artesanos />} />
          <Route path="/piezas/nueva" element={<PiezaForm />} />
          <Route path="/piezas/:id/editar" element={<PiezaForm />} />
          <Route path="/piezas/:id" element={<PiezaDetalle />} />
          <Route path="/piezas" element={<Piezas />} />
          <Route path="/inventario" element={<Inventario />} />
          <Route path="/insumos" element={<Insumos />} />
          <Route path="/produccion" element={<Produccion />} />
          {can('visits') && <Route path="/visitas" element={<Visitas />} />}
          {can('nfc', 'revocations') && <Route path="/certificacion" element={<Certificacion />} />}
          {can('nfc', 'revocations') && <Route path="/certificacion/:id" element={<CertificarPieza />} />}
          {can('design') && <Route path="/diseno/:id" element={<DisenoCertificado />} />}
          {roles.includes('owner') && <Route path="/usuarios" element={<Usuarios />} />}
          {can('hero') && <Route path="/hero" element={<Hero />} />}
          <Route path="/archivados" element={<Apartado key="archivados" mode="archivados" />} />
          <Route path="/papelera" element={<Apartado key="papelera" mode="papelera" />} />
          <Route path="/auditoria" element={<Auditoria />} />
          <Route path="*" element={<Navigate to="/resumen" replace />} />
        </Routes>
        </PermissionsContext.Provider>
        </RolesContext.Provider>
      </main>
    </div>
  );
}
