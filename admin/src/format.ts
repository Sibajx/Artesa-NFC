import type { PublicationStatus } from './api';

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
