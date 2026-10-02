import { Link } from 'react-router-dom';
import type { AdminMedia, ArtisanDetail, PieceDetail } from './api';

// Status banner + pre-publication review for an artisan or a piece.
// It answers "¿se ve en el sitio?" at a glance, lists what is missing before
// publishing (blocking items and recommendations) and links to the public page.

// Public site (same paths in frontend/ and web/).
const SITE_ORIGIN: string = import.meta.env.VITE_SITE_ORIGIN ?? 'https://artesanfc.com';

interface Check {
  ok: boolean;
  label: string;
  // Blocking: publishing fails or the record stays invisible without it.
  blocking?: boolean;
  hint?: string;
  link?: { to: string; label: string };
}

const hasActive = (media: AdminMedia[], roles: string[]) =>
  media.some((m) => m.status === 'active' && roles.includes(m.media.role));

const filled = (v: unknown) => (Array.isArray(v) ? v.length > 0 : typeof v === 'string' ? v.trim() !== '' : v != null);

function artisanChecks(a: ArtisanDetail): Check[] {
  return [
    { ok: filled(a.full_name), label: 'Nombre completo', blocking: true },
    { ok: hasActive(a.media, ['portrait']), label: 'Foto de retrato', hint: 'Súbela en Medios con el uso «Retrato».' },
    { ok: filled(a.biography), label: 'Biografía' },
    { ok: filled(a.locality) || filled(a.municipality), label: 'Lugar de origen (localidad o municipio)' },
    { ok: filled(a.techniques), label: 'Técnicas' },
  ];
}

function pieceChecks(p: PieceDetail): Check[] {
  return [
    { ok: filled(p.name), label: 'Nombre de la pieza', blocking: true },
    {
      ok: p.artisan.publication_status === 'published',
      label: 'Artesano publicado',
      blocking: true,
      hint: 'El sitio solo muestra piezas cuyo artesano también está publicado.',
      link: p.artisan.publication_status === 'published' ? undefined : { to: `/artesanos/${p.artisan.id}`, label: `Ir a ${p.artisan.full_name}` },
    },
    { ok: hasActive(p.media, ['hero']), label: 'Foto de portada', hint: 'Súbela en Medios con el uso «Portada».' },
    { ok: filled(p.description), label: 'Descripción' },
    { ok: filled(p.technique), label: 'Técnica' },
    { ok: filled(p.materials), label: 'Materiales' },
  ];
}

type Props = { kind: 'artisans'; record: ArtisanDetail } | { kind: 'pieces'; record: PieceDetail };

export function PublishChecklist(props: Props) {
  const { kind, record } = props;
  if (record.publication_status === 'archived' || record.trashed_at) return null;
  const visible = kind === 'artisans' ? record.publication_status === 'published' : props.record.publicly_visible;
  const checks = kind === 'artisans' ? artisanChecks(props.record) : pieceChecks(props.record);
  const missing = checks.filter((c) => !c.ok);
  const blocking = missing.filter((c) => c.blocking);
  const publicUrl = `${SITE_ORIGIN}/${kind === 'artisans' ? 'artesanos' : 'piezas'}/${encodeURIComponent(record.slug)}/`;
  const noun = kind === 'artisans' ? 'Este artesano' : 'Esta pieza';

  if (visible) {
    return (
      <section aria-label="Estado en el sitio" className="rounded-xl border border-botanica-jade/30 bg-botanica-jade/5 p-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-medium text-botanica-negro">Visible en el sitio</p>
          {missing.length > 0 && (
            <p className="text-sm text-botanica-grafito mt-1">
              Para que se vea completo, falta: {missing.map((c) => c.label.toLowerCase()).join(', ')}.
            </p>
          )}
        </div>
        <a href={publicUrl} target="_blank" rel="noopener noreferrer" className="btn-secondary">Ver en el sitio ↗</a>
      </section>
    );
  }

  const status = record.publication_status === 'draft'
    ? `${noun} está en borrador: no se ve en el sitio.`
    : `${noun} está publicada, pero todavía no se ve en el sitio.`;

  return (
    <section aria-labelledby={`review-${record.id}`} className="rounded-xl border border-amber-300 bg-amber-50 p-5 flex flex-col gap-3">
      <div>
        <h2 id={`review-${record.id}`} className="font-medium text-botanica-negro">{status}</h2>
        <p className="text-sm text-botanica-grafito mt-1">
          {blocking.length > 0
            ? 'Antes de que se vea hay que resolver lo marcado como obligatorio.'
            : record.publication_status === 'draft'
              ? 'Cuando esté lista, usa «Publicar» (arriba).'
              : 'Revisa lo marcado abajo.'}
        </p>
      </div>
      <ul className="flex flex-col gap-1.5 text-sm">
        {checks.map((c) => (
          <li key={c.label} className="flex flex-wrap items-baseline gap-x-2">
            <span aria-hidden="true" className={c.ok ? 'text-botanica-jade' : c.blocking ? 'text-red-700' : 'text-amber-700'}>
              {c.ok ? '✓' : c.blocking ? '✕' : '!'}
            </span>
            <span className={c.ok ? 'text-botanica-grafito' : 'text-botanica-negro'}>
              {c.label}
              <span className="sr-only">{c.ok ? ': listo' : c.blocking ? ': obligatorio, falta' : ': recomendado, falta'}</span>
              {!c.ok && c.blocking && <span className="ml-1 text-xs text-red-700">(obligatorio)</span>}
            </span>
            {!c.ok && c.hint && <span className="text-xs text-botanica-gris">{c.hint}</span>}
            {!c.ok && c.link && <Link to={c.link.to} className="text-xs underline text-botanica-jade">{c.link.label}</Link>}
          </li>
        ))}
      </ul>
      <p className="text-xs text-botanica-gris">Dirección pública al publicar: <span className="font-mono">{publicUrl}</span></p>
    </section>
  );
}
