import { useState } from 'react';
import type { ReactNode } from 'react';
import { LOGOUT_URL } from './api';
import type { ApiError, PublicationStatus } from './api';
import { labels } from './format';
import { usePermissions } from './roles-context';
import { applyTheme, readTheme } from './theme';
import type { ThemeChoice } from './theme';

const TONES = {
  jade: 'bg-botanica-jade/10 text-botanica-jade border-botanica-jade/20',
  neutral: 'bg-botanica-hueso text-botanica-grafito border-botanica-gris/20',
  lavanda: 'bg-botanica-lavanda/20 text-botanica-negro border-botanica-lavanda/30',
};

export function Badge({ tone, children }: { tone: keyof typeof TONES; children: ReactNode }) {
  return <span className={`px-2.5 py-1 rounded-full text-xs font-medium border whitespace-nowrap ${TONES[tone]}`}>{children}</span>;
}

export function PublicationBadge({ status }: { status: PublicationStatus }) {
  const tone = status === 'published' ? 'jade' : status === 'archived' ? 'lavanda' : 'neutral';
  return <Badge tone={tone}>{labels.publication(status)}</Badge>;
}

export function Loading({ label }: { label: string }) {
  return (
    <div role="status" className="p-12 text-center text-botanica-gris">
      {label}
    </div>
  );
}

const ERROR_TEXT: Record<ApiError['kind'], { title: string; body: string }> = {
  session: { title: 'Tu sesión expiró', body: 'Vuelve a cargar la página para iniciar sesión de nuevo.' },
  forbidden: {
    title: 'Sin acceso',
    body: 'Tu cuenta inició sesión, pero no está autorizada para usar Gestión. Pide que agreguen tu email.',
  },
  not_found: { title: 'No encontrado', body: 'El registro no existe.' },
  conflict: { title: 'No se pudo guardar', body: 'El registro cambió o la acción no aplica. Recarga la página.' },
  invalid: { title: 'Solicitud inválida', body: 'Revisa el enlace o los filtros.' },
  unavailable: { title: 'Servicio no disponible', body: 'La API no respondió. Intenta de nuevo en unos minutos.' },
  network: { title: 'Sin conexión', body: 'No se pudo contactar al servidor. Revisa tu conexión.' },
};

export function ErrorState({ error }: { error: ApiError }) {
  const text = ERROR_TEXT[error.kind];
  return (
    <div role="alert" className="p-10 text-center">
      <p className="text-lg font-serif text-botanica-negro mb-2">{text.title}</p>
      <p className="text-sm text-botanica-grafito mb-5">{text.body}</p>
      {error.kind === 'session' && (
        <button type="button" onClick={() => window.location.reload()} className="btn-primary">
          Volver a cargar
        </button>
      )}
      {error.kind === 'forbidden' && (
        <a href={LOGOUT_URL} className="btn-secondary">
          Cerrar sesión
        </a>
      )}
    </div>
  );
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) {
  return (
    <header className="mb-8 flex flex-col md:flex-row md:items-end justify-between gap-4">
      <div>
        <h1 className="text-4xl font-serif text-botanica-negro tracking-tight mb-2">{title}</h1>
        {subtitle && <p className="text-botanica-grafito text-lg">{subtitle}</p>}
      </div>
      {children}
    </header>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-sm font-medium text-botanica-gris mb-1">{label}</dt>
      <dd className="text-botanica-negro">{children ?? '—'}</dd>
    </div>
  );
}

// What an account may do follows its permission checkboxes (/me). The API checks
// each one again; these only keep the screen honest, so nobody clicks a button
// that is going to answer 403.

/** Shows ``children`` only to accounts that hold ``permission``. */
export function Can({ permission, children }: { permission: string; children: ReactNode }) {
  return usePermissions().includes(permission) ? <>{children}</> : null;
}

/** Keeps the content visible but greyed out and unclickable without ``permission``. */
export function Gate({ permission, children }: { permission: string; children: ReactNode }) {
  if (usePermissions().includes(permission)) return <>{children}</>;
  return (
    <fieldset disabled title="Tu cuenta no tiene permiso para esta acción" className="m-0 min-w-0 border-0 p-0 opacity-60">
      {children}
    </fieldset>
  );
}

const THEMES: { value: ThemeChoice; label: string; icon: ReactNode }[] = [
  { value: 'light', label: 'Claro', icon: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></> },
  { value: 'dark', label: 'Oscuro', icon: <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /> },
  { value: 'system', label: 'Automático (sigue al sistema)', icon: <><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></> },
];

/** Claro / oscuro / automático; recuerda la elección en el navegador. */
export function ThemeToggle() {
  const [choice, setChoice] = useState<ThemeChoice>(readTheme);
  return (
    <div role="group" aria-label="Tema de color" className="flex items-center gap-1 rounded-full border border-botanica-gris/25 p-0.5">
      {THEMES.map((t) => (
        <button key={t.value} type="button" aria-pressed={choice === t.value} aria-label={t.label} title={t.label}
          onClick={() => { setChoice(t.value); applyTheme(t.value); }}
          className={`grid h-7 w-7 place-items-center rounded-full ${choice === t.value ? 'bg-botanica-jade text-[#1a0f05]' : 'text-botanica-gris hover:text-botanica-negro'}`}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{t.icon}</svg>
        </button>
      ))}
    </div>
  );
}
