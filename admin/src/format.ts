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
  sold: 'Vendida',
};

const CERTIFICATE_LABELS: Record<string, string> = { draft: 'Borrador', active: 'Activo', revoked: 'Revocado' };

const NFC_LABELS: Record<string, string> = {
  available: 'Disponible',
  programmed: 'Programado',
  locked: 'Bloqueado',
  replaced: 'Reemplazado',
  retired: 'Retirado',
};

const ROLE_LABELS: Record<string, string> = {
  hero: 'Portada',
  gallery: 'Galería',
  detail: 'Detalle',
  process: 'Proceso',
  portrait: 'Retrato',
  model_3d: 'Modelo 3D',
};

// Why a trashed record cannot be deleted for good (services/trash.py codes).
export const PURGE_BLOCKERS: Record<string, string> = {
  may_have_been_public: 'Ya estuvo publicado: puede quedarse en la papelera, pero no eliminarse definitivamente.',
  has_certificate: 'Tiene un certificado emitido (Certificación): no se puede eliminar.',
  has_nfc_tag: 'Tiene un chip NFC asignado (Certificación): no se puede eliminar.',
  has_sale: 'Tiene una venta registrada: no se puede eliminar.',
  has_pieces: 'Tiene piezas: elimínalas primero desde la papelera.',
};

export const labels = {
  role: (s: string) => ROLE_LABELS[s] ?? s,
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
  active_certificate: 'La pieza tiene un certificado activo. Revócalo en Certificación antes de archivarla.',
  unknown_artisan: 'El artesano seleccionado no existe.',
  archived_artisan: 'El artesano está archivado; restáuralo o elige otro.',
  archived: 'El registro está archivado; restáuralo antes de agregar medios.',
  media_not_configured: 'El almacenamiento de medios todavía no está configurado en el servidor.',
  too_large: 'El archivo es demasiado grande: fotos hasta 25 MB, videos hasta 4 MB y modelos 3D hasta 8 MB.',
  empty_file: 'El archivo está vacío.',
  unsupported_type: 'Formato no admitido. Sube una foto JPEG, PNG o WebP, un video MP4 o un modelo GLB.',
  unsupported_media_type: 'Formato no admitido. Sube una foto JPEG, PNG o WebP, un video MP4 o un modelo GLB.',
  image_too_large: 'La foto tiene más de 50 megapíxeles.',
  alt_text_required: 'Las fotos necesitan una descripción (texto alternativo).',
  invalid_role: 'Ese uso no está disponible aquí.',
  wrong_type_for_role: 'Ese tipo de archivo no corresponde al uso elegido (la portada es una foto; el modelo 3D, un GLB).',
  video_has_audio: 'El video tiene sonido. Expórtalo de nuevo sin la pista de audio.',
  video_has_location: 'El video guarda la ubicación GPS. Expórtalo de nuevo sin ubicación.',
  invalid_video: 'El video está dañado o no es un MP4 válido.',
  invalid_model: 'El modelo 3D no es un archivo GLB (glTF 2.0) válido.',
  trashed: 'Está en la papelera. Restáuralo primero.',
  trashed_artisan: 'Su artesano está en la papelera. Restáuralo primero.',
  published: 'Está publicado: pásalo a borrador antes de enviarlo a la papelera.',
  has_pieces: 'Primero envía a la papelera (o elimina) las piezas de este artesano.',
  has_certificate: 'La pieza tiene un certificado (se gestiona en Certificación); no se puede eliminar.',
  has_nfc_tag: 'La pieza tiene un chip NFC (se gestiona en Certificación); no se puede eliminar.',
  has_sale: 'La pieza tiene una venta registrada; no se puede eliminar.',
  not_trashed: 'Primero envíalo a la papelera.',
  may_have_been_public: 'Ya pudo verse en el sitio, así que no se puede eliminar definitivamente: archívalo o déjalo en la papelera.',
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
