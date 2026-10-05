import { useState } from 'react';
import { ApiError, adminApi } from './api';
import type { ArtisanDetail } from './api';
import { formatDateTime, writeErrorMessage } from './format';
import { useConfirm, useToast } from './feedback-context';
import { whatsappUrl } from './whatsapp';

// P-026 G3: the artisan authorizes the publication of their name, portrait
// and story through a WhatsApp link (or the team records it in person, and
// can later ask for a confirmation by WhatsApp). The artisan can also ask
// for changes (shown here with their comment) or decline (the API unpublishes).

const CONFLICTS: Record<string, string> = {
  already_authorized: 'El artesano ya autorizó la publicación por WhatsApp.',
  not_authorized: 'No hay autorización que retirar.',
  invalid_authorization: 'Escribe una nota de al menos 5 letras.',
};

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && CONFLICTS[e.code]) return CONFLICTS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

export function AutorizacionSection({ artisan, onChanged }: { artisan: ArtisanDetail; onChanged: () => void }) {
  const auth = artisan.authorization;
  const [link, setLink] = useState<{ url: string; whatsapp: string | null; contact: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const confirm = useConfirm();
  const toast = useToast();
  const name = artisan.artistic_name || artisan.full_name;
  const contact = artisan.validation_contact_name;

  async function ask() {
    setError(null);
    try {
      const r = await adminApi.requestAuthorization(artisan.id);
      // No reload here: the page would unmount this section and lose the link.
      setLink({ url: r.url, whatsapp: r.whatsapp, contact: r.contact_name });
    } catch (e) {
      setError(message(e));
    }
  }

  async function withNote(kind: 'record' | 'revoke') {
    const note = await confirm(kind === 'record'
      ? { title: 'Registrar autorización en persona', body: 'Para cuando el artesano autorizó sin el enlace (por ejemplo, firmó una hoja en el taller).', confirmLabel: 'Registrar', reason: { label: 'Cómo autorizó', placeholder: 'Ej. firmó la hoja impresa en su taller' } }
      : { title: '¿Retirar la autorización?', body: 'El artesano ya no podrá publicarse hasta volver a autorizar. Si ya está publicado, decide aparte si lo regresas a borrador.', confirmLabel: 'Retirar', tone: 'danger', reason: { label: 'Motivo', placeholder: 'Ej. pidió quitar su historia' } });
    if (note === null) return;
    setError(null);
    try {
      if (kind === 'record') await adminApi.recordAuthorization(artisan.id, note.trim());
      else await adminApi.revokeAuthorization(artisan.id, note.trim());
      toast(kind === 'record' ? 'Autorización registrada' : 'Autorización retirada');
      onChanged();
    } catch (e) {
      setError(message(e));
    }
  }

  const whatsappText = (url: string) =>
    `Hola${contact ? ` ${contact}` : ''}. Somos de ArtesaNFC. ${contact ? `Por favor enséñale a ${name} este enlace` : 'Este enlace muestra'} todo lo que aparece de ${contact ? 'él' : 'ti'} en artesanfc.com: nombre, foto, historia, contacto y piezas. Si ${contact ? 'está de acuerdo' : 'estás de acuerdo'}, toca "Sí, autorizo"; si algo no está bien, toca "Quiero cambios" y ${contact ? 'escriban' : 'escribe'} qué corregir: ${url}`;

  const answer = artisan.last_answer;
  const inPerson = auth?.status === 'authorized' && auth.medium !== 'whatsapp';
  const confirming = inPerson && !!auth?.expires_at;
  const missingWhatsapp = !artisan.validation_whatsapp && (
    <p className="text-amber-800">Falta su WhatsApp: agrégalo en "Editar" (sección "WhatsApp para aprobaciones").</p>
  );
  const linkReady = link && (
    <div className="flex flex-col gap-2 rounded-xl border border-botanica-jade/30 bg-botanica-jade/5 p-4">
      <p>Enlace listo (vence en 14 días). Ábrelo en WhatsApp para mandarlo{link.contact ? ` a ${link.contact}` : ''}:</p>
      <div className="flex flex-wrap gap-2">
        <a className="btn-primary" target="_blank" rel="noreferrer" href={whatsappUrl(link.whatsapp, whatsappText(link.url))}>Abrir WhatsApp</a>
        <button type="button" className="btn-secondary" onClick={() => void navigator.clipboard?.writeText(link.url).then(() => toast('Enlace copiado'), () => undefined)}>Copiar enlace</button>
        <button type="button" className="btn-secondary" onClick={() => { setLink(null); onChanged(); }}>Listo</button>
      </div>
    </div>
  );

  return (
    <section aria-labelledby="auth-heading" className="card-elevated p-5 sm:p-6 flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="auth-heading" className="text-xl font-serif text-botanica-negro">Autorización para publicar</h2>
        <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${auth?.status === 'authorized' ? 'bg-botanica-jade/10 text-botanica-jade' : 'bg-amber-50 text-amber-800'}`}>
          {auth?.status === 'authorized' ? 'Autorizado' : auth?.status === 'pending' ? 'Esperando respuesta'
            : answer?.status === 'changes_requested' ? 'Pidió cambios' : answer?.status === 'declined' ? 'No autorizó' : 'Sin autorización'}
        </span>
      </div>
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      {answer && (
        <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 flex flex-col gap-1">
          <p className="font-medium">
            {answer.status === 'changes_requested'
              ? `${name} pidió cambios${answer.decided_at ? ` el ${formatDateTime(answer.decided_at)}` : ''}:`
              : `${name} no autorizó${answer.decided_at ? ` el ${formatDateTime(answer.decided_at)}` : ''}. Se pasó a borrador junto con sus piezas.`}
          </p>
          {answer.note && <blockquote className="border-l-2 border-amber-300 pl-3 italic">{answer.note}</blockquote>}
          {answer.status === 'changes_requested' && (
            <p>Sigue publicado. Corrige su ficha (o sus piezas) y mándale un enlace nuevo para que vea cómo quedó.</p>
          )}
        </div>
      )}

      {auth?.status === 'authorized' ? (
        <div className="text-sm text-botanica-grafito flex flex-col gap-2">
          <p>Autorizó {auth.medium === 'whatsapp' ? 'por WhatsApp' : 'en persona'}{auth.decided_at ? ` el ${formatDateTime(auth.decided_at)}` : ''}.{auth.note ? ` Nota: ${auth.note}` : ''}</p>
          {inPerson && !link && (
            <p>
              {confirming && auth.expires_at
                ? `Ya se le pidió que lo confirme por WhatsApp; el enlace vence el ${formatDateTime(auth.expires_at)}. Mientras tanto la autorización sigue valiendo.`
                : 'Puedes pedirle que lo confirme por WhatsApp: verá todo lo publicado y la constancia quedará con su respuesta. Mientras no conteste, la autorización en persona sigue valiendo.'}
            </p>
          )}
          {inPerson && missingWhatsapp}
          {linkReady || (
            <div className="flex flex-wrap gap-2">
              {inPerson && (
                <button type="button" className="btn-primary" onClick={() => void ask()}>
                  {confirming ? 'Mandar un enlace nuevo por WhatsApp' : 'Pedir confirmación por WhatsApp'}
                </button>
              )}
              <button type="button" className="btn-secondary" onClick={() => void withNote('revoke')}>Retirar autorización</button>
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-3 text-sm">
          <p className="text-botanica-grafito">
            Antes de publicar a {name}, debe autorizarlo. Le llega un enlace por WhatsApp donde ve todo lo que se publica de él
            (foto, biografía, historia, técnicas, contacto y piezas) y responde <strong>"Sí, autorizo"</strong>,
            <strong> "Quiero cambios"</strong> o <strong>"No autorizo"</strong>. Si no autoriza, se pasa a borrador junto con sus piezas.
          </p>
          {missingWhatsapp}
          {auth?.status === 'pending' && auth.expires_at && !link && (
            <p className="text-botanica-gris">Ya se le mandó un enlace; vence el {formatDateTime(auth.expires_at)}. Si lo perdió, manda uno nuevo.</p>
          )}
          {linkReady || (
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary" onClick={() => void ask()}>
                {auth?.status === 'pending' || answer ? 'Mandar un enlace nuevo por WhatsApp' : 'Pedir autorización por WhatsApp'}
              </button>
              <button type="button" className="btn-secondary" onClick={() => void withNote('record')}>Autorizó en persona</button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
