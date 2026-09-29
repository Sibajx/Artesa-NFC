import type { ApiError, PublicationStatus } from './api';

const DATE = new Intl.DateTimeFormat('es-MX', { dateStyle: 'medium', timeZone: 'America/Mexico_City' });
const DATE_TIME = new Intl.DateTimeFormat('es-MX', {
  dateStyle: 'medium',
  timeStyle: 'short',
  timeZone: 'America/Mexico_City',
});

export function formatDate(value: string | null | undefined): string {
  return value ? DATE.format(new Date(value)) : '—';
}

export function formatDateTime(value: string | null | undefined): string {
  return value ? DATE_TIME.format(new Date(value)) : '—';
}

const PUBLICATION_LABELS: Record<PublicationStatus, string> = {
  draft: 'Borrador',
  published: 'Publicado',
  archived: 'Archivado',
};

const AVAILABILITY_LABELS: Record<string, string> = {
  available: 'Disponible',
  reserved: 'Reservada',
  exhibited: 'En exhibición',
  archived: 'Archivada',
};

const CERTIFICATE_LABELS: Record<string, string> = { draft: 'Borrador', active: 'Activo', revoked: 'Revocado' };

const NFC_LABELS: Record<string, string> = {
  available: 'Disponible',
  programmed: 'Programado',
  locked: 'Bloqueado',
  replaced: 'Reemplazado',
  retired: 'Retirado',
};

export const labels = {
  publication: (s: PublicationStatus) => PUBLICATION_LABELS[s] ?? s,
  availability: (s: string) => AVAILABILITY_LABELS[s] ?? s,
  certificate: (s: string) => CERTIFICATE_LABELS[s] ?? s,
  nfc: (s: string) => NFC_LABELS[s] ?? s,
};

export function joinList(value: unknown[] | null): string {
  return value && value.length ? value.map(String).join(', ') : '—';
}

const CONFLICT_TEXT: Record<string, string> = {
  stale: 'Alguien más modificó este registro después de que lo abriste. Recarga la página para ver la versión actual y vuelve a intentarlo.',
  precondition_required: 'Recarga la página y vuelve a intentarlo.',
  draft_only: 'Ese dato solo se puede cambiar mientras el registro está en borrador.',
  invalid_transition: 'Esa acción no aplica al estado actual del registro.',
  incomplete: 'Faltan datos obligatorios para publicar.',
  has_published_pieces: 'Primero pasa a borrador o archiva las piezas publicadas de este artesano.',
  active_certificate: 'La pieza tiene un certificado activo. Revócalo con la CLI de provisioning antes de archivarla.',
  unknown_artisan: 'El artesano seleccionado no existe.',
  archived_artisan: 'El artesano está archivado; restáuralo o elige otro.',
};

const DUPLICATE_TEXT: Record<string, string> = {
  slug: 'Ese identificador para la URL ya está en uso.',
  public_code: 'Ese código público ya está en uso.',
};

// A Spanish message for a failed write; the server's English text is never shown.
export function writeErrorMessage(error: ApiError): string {
  if (error.code === 'duplicate') return DUPLICATE_TEXT[error.field ?? ''] ?? 'Ese valor ya está en uso.';
  if (error.code && CONFLICT_TEXT[error.code]) return CONFLICT_TEXT[error.code];
  if (error.kind === 'invalid') return 'Revisa los campos marcados.';
  if (error.kind === 'session') return 'Tu sesión expiró. Recarga la página para iniciar sesión de nuevo.';
  if (error.kind === 'forbidden') return 'Tu cuenta no tiene permiso para esta acción.';
  if (error.kind === 'not_found') return 'El registro ya no existe.';
  return 'No se pudo guardar. Intenta de nuevo en unos minutos.';
}

export function splitList(value: string): string[] | null {
  const items = value.split(',').map((item) => item.trim()).filter(Boolean);
  return items.length ? items : null;
}

export function blankToNull(value: string): string | null {
  return value.trim() ? value.trim() : null;
}
