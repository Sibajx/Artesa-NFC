// P-026 G7: audit actions in plain Spanish. Unknown codes fall back to the
// code itself, so a new action never breaks the page.
const LABELS: Record<string, string> = {
  'artisan.created': 'Creó un artesano',
  'artisan.updated': 'Editó un artesano',
  'artisan.published': 'Publicó un artesano',
  'artisan.unpublished': 'Regresó un artesano a borrador',
  'artisan.archived': 'Archivó un artesano',
  'artisan.restored': 'Restauró un artesano',
  'artisan.trashed': 'Mandó un artesano a la papelera',
  'artisan.untrashed': 'Sacó un artesano de la papelera',
  'artisan.deleted': 'Eliminó un artesano',
  'artisan.authorization_requested': 'Pidió la autorización del artesano por WhatsApp',
  'artisan.authorized': 'El artesano autorizó su publicación',
  'artisan.authorization_declined': 'El artesano no autorizó su publicación (se pasó a borrador)',
  'artisan.authorization_changes_requested': 'El artesano pidió cambios antes de autorizar',
  'artisan.authorization_revoked': 'Retiró la autorización del artesano',
  'piece.created': 'Creó una pieza',
  'piece.updated': 'Editó una pieza',
  'piece.published': 'Publicó una pieza',
  'piece.unpublished': 'Regresó una pieza a borrador',
  'piece.archived': 'Archivó una pieza',
  'piece.restored': 'Restauró una pieza',
  'piece.trashed': 'Mandó una pieza a la papelera',
  'piece.untrashed': 'Sacó una pieza de la papelera',
  'piece.deleted': 'Eliminó una pieza',
  'piece.availability_changed': 'Cambió la disponibilidad',
  'piece.palette_set': 'Cambió los colores del certificado',
  'piece.sold': 'Registró una venta',
  'piece.sale_cancelled': 'Canceló una venta',
  'media.uploaded': 'Subió un archivo',
  'media.updated': 'Editó un archivo',
  'media.archived': 'Archivó un archivo',
  'media.restored': 'Restauró un archivo',
  'media.deleted': 'Eliminó un archivo',
  'media.replaced': 'Reemplazó un archivo',
  'custody.issued': 'Emitió el certificado',
  'custody.rotated': 'Reemplazó el certificado o el chip',
  'custody.programmed': 'Grabó el chip',
  'custody.locked': 'Bloqueó el chip',
  'custody.revoked': 'Revocó el certificado',
  'custody.denied': 'Intentó entrar a Certificación sin permiso',
  'custody.card_issued': 'Generó la tarjeta del comprador',
  'custody.card_replaced': 'Repuso la tarjeta del comprador',
  'custody.card_blocked': 'Bloqueó la tarjeta del comprador',
  'custody.card_unblocked': 'Desbloqueó la tarjeta del comprador',
  'custody.claim_released': 'Liberó el registro del dueño',
  'custody.transferred': 'Transfirió la pieza a un nuevo dueño',
  'custody.reported_stolen': 'Reportó la pieza como robada',
  'custody.stolen_cleared': 'Quitó el reporte de robo',
  'ownership.unlocked': 'Se abrió el certificado original',
  'ownership.claimed': 'El comprador registró la pieza',
  'ownership.unlock_failed': 'Intento fallido de abrir el certificado original',
  'design.created': 'Empezó un diseño de certificado',
  'design.edited': 'Editó un diseño de certificado',
  'design.submitted': 'Mandó un diseño al artesano',
  'design.approved': 'Se aprobó un diseño de certificado',
  'design.changes_requested': 'El artesano pidió cambios a un diseño',
  'design.published': 'Publicó un diseño de certificado',
  'design.discarded': 'Descartó un diseño de certificado',
  'account.added': 'Dio acceso a Gestión',
  'account.role_changed': 'Cambió el rol de una cuenta',
  'account.removed': 'Quitó el acceso a Gestión',
};

export const actionLabel = (action: string): string => LABELS[action] ?? action;

export const AUDIT_CATEGORIES: { value: string; label: string }[] = [
  { value: '', label: 'Todo' },
  { value: 'artisan.', label: 'Artesanos' },
  { value: 'piece.', label: 'Piezas y ventas' },
  { value: 'media.', label: 'Fotos y archivos' },
  { value: 'custody.', label: 'Certificación y chips' },
  { value: 'ownership.', label: 'Certificado original (público)' },
  { value: 'design.', label: 'Diseños de certificado' },
  { value: 'account.', label: 'Usuarios' },
];

/** Who did it: an email, or the public link (no account) for system events. */
export const actorLabel = (email: string | null, actorType: string): string =>
  email ?? (actorType === 'system' ? 'Enlace público / sistema' : actorType);

/** Where to see the record the event is about, when it is a page in Gestión. */
export function entityLink(entityType: string, entityId: string): string | null {
  if (entityType === 'piece') return `/piezas/${entityId}`;
  if (entityType === 'artisan') return `/artesanos/${entityId}`;
  return null;
}
