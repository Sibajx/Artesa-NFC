import { useState } from 'react';
import { ApiError, adminApi } from './api';
import type { CardKey, CustodyState } from './api';
import { formatDateTime, writeErrorMessage } from './format';
import { useConfirm, useToast } from './feedback-context';
import { Badge } from './ui';

// ADR-030 phase 3: the buyer's scratch card, the claim and the stolen report.
// The card key arrives once (issue / replace / transfer), lives only in this
// component's memory for printing, and is dropped when the custodian confirms
// it was printed or leaves the page.

const CONFLICTS: Record<string, string> = {
  no_active_certificate: 'Primero emite el certificado y graba el chip.',
  card_exists: 'La pieza ya tiene tarjeta. Usa "Reponer tarjeta".',
  no_card: 'La pieza todavía no tiene tarjeta.',
  card_already_blocked: 'La tarjeta ya está bloqueada.',
  card_not_blocked: 'La tarjeta no está bloqueada.',
  not_claimed: 'Nadie ha reclamado esta pieza.',
  already_reported_stolen: 'La pieza ya está reportada como robada.',
  not_reported_stolen: 'La pieza no está reportada como robada.',
};

type NoteAction = 'card/replace' | 'transfer' | 'card/block' | 'card/unblock' | 'claim/release' | 'stolen' | 'stolen/clear';

const ACTIONS: Record<NoteAction, { title: string; body: string; confirm: string; danger?: boolean; done: string }> = {
  'card/replace': {
    title: 'Reponer tarjeta',
    body: 'Para tarjeta perdida o clave vista por otra persona. La clave actual deja de servir y se genera una nueva para imprimir. El reclamo y el PIN del dueño se conservan.',
    confirm: 'Generar tarjeta nueva',
    done: 'Tarjeta repuesta',
  },
  transfer: {
    title: 'Transferir a un nuevo dueño',
    body: 'Para venta o regalo. Se libera el reclamo del dueño actual y se genera una tarjeta nueva para el nuevo dueño.',
    confirm: 'Transferir',
    danger: true,
    done: 'Pieza transferida',
  },
  'card/block': {
    title: 'Bloquear tarjeta',
    body: 'Para robo de la cartera o de la tarjeta. Nadie podrá abrir el certificado original con ella hasta que la desbloquees o la repongas.',
    confirm: 'Bloquear',
    danger: true,
    done: 'Tarjeta bloqueada',
  },
  'card/unblock': {
    title: 'Desbloquear tarjeta',
    body: 'Vuelve a habilitar la tarjeta y borra los intentos fallidos.',
    confirm: 'Desbloquear',
    done: 'Tarjeta desbloqueada',
  },
  'claim/release': {
    title: 'Liberar el reclamo',
    body: 'Para un dueño que olvidó su PIN. Verifica que quien lo pide es el correo registrado. Después podrá reclamar de nuevo con su tarjeta y elegir otro PIN.',
    confirm: 'Liberar',
    danger: true,
    done: 'Reclamo liberado',
  },
  stolen: {
    title: 'Reportar la pieza como robada',
    body: 'Quien escanee el chip verá "Pieza reportada como robada" y el certificado original quedará cerrado.',
    confirm: 'Reportar robo',
    danger: true,
    done: 'Pieza reportada como robada',
  },
  'stolen/clear': {
    title: 'Quitar el reporte de robo',
    body: 'Para una pieza recuperada. El chip vuelve a mostrarse normal.',
    confirm: 'Quitar reporte',
    done: 'Reporte de robo retirado',
  },
};

function message(e: unknown): string {
  if (e instanceof ApiError && e.code && CONFLICTS[e.code]) return CONFLICTS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

export default function TarjetaComprador({ state, reload, setError }: {
  state: CustodyState;
  reload: () => void;
  setError: (message: string | null) => void;
}) {
  const [printable, setPrintable] = useState<CardKey | null>(null);
  const confirm = useConfirm();
  const toast = useToast();
  const { card, claim } = state;
  const lockedUntil = card?.locked_until && new Date(card.locked_until) > new Date() ? card.locked_until : null;

  async function issue() {
    setError(null);
    try {
      setPrintable(await adminApi.cardIssue(state.piece_id));
      reload();
    } catch (e) {
      setError(message(e));
    }
  }

  async function run(action: NoteAction) {
    const spec = ACTIONS[action];
    const note = await confirm({
      title: spec.title,
      body: spec.body,
      confirmLabel: spec.confirm,
      tone: spec.danger ? 'danger' : 'default',
      reason: { label: 'Nota (qué revisaste como prueba)', placeholder: 'Ej. ticket de compra, correo del dueño' },
    });
    if (note === null) return;
    if (note.trim().length < 5) {
      setError('Escribe una nota de al menos 5 letras: queda en la auditoría como prueba.');
      return;
    }
    setError(null);
    try {
      if (action === 'card/replace' || action === 'transfer') {
        setPrintable(await adminApi.cardKeyAction(state.piece_id, action, note.trim()));
      } else {
        await adminApi.ownershipAction(state.piece_id, action, note.trim());
      }
      toast(spec.done);
      reload();
    } catch (e) {
      setError(message(e));
    }
  }

  async function printed() {
    const answer = await confirm({
      title: '¿Ya imprimiste la tarjeta?',
      body: 'La clave no se vuelve a mostrar. Si se pierde antes de imprimirla, tendrás que reponer la tarjeta.',
      confirmLabel: 'Sí, ya la imprimí',
    });
    if (answer !== null) setPrintable(null);
  }

  if (printable) return <Printable card={printable} name={state.name} onPrinted={() => void printed()} />;

  return (
    <section className="card-elevated p-5 sm:p-6 flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xl font-serif text-botanica-negro">Tarjeta del comprador</h2>
        <div className="flex flex-wrap gap-2">
          {card && <Badge tone={card.status === 'active' && !lockedUntil ? 'jade' : 'lavanda'}>
            {card.status === 'blocked' ? 'Tarjeta bloqueada' : lockedUntil ? 'Bloqueo temporal' : 'Tarjeta activa'}
          </Badge>}
          <Badge tone={claim ? 'jade' : 'neutral'}>{claim ? 'Reclamada' : 'Sin reclamar'}</Badge>
        </div>
      </div>

      {state.reported_stolen_at && (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          Pieza reportada como robada el {formatDateTime(state.reported_stolen_at)}. El certificado original está cerrado.
        </p>
      )}

      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-botanica-gris">Tarjeta</dt>
          <dd className="text-botanica-negro">{card ? `Emitida ${formatDateTime(card.issued_at)}` : 'Sin tarjeta'}</dd>
          {card && card.failed_attempts > 0 && (
            <dd className="text-botanica-grafito">
              {card.failed_attempts} intento(s) fallido(s){lockedUntil ? ` · bloqueada hasta ${formatDateTime(lockedUntil)}` : ''}
            </dd>
          )}
        </div>
        <div>
          <dt className="text-botanica-gris">Dueño</dt>
          <dd className="text-botanica-negro break-all">{claim ? claim.owner_email : 'Aún no reclama la pieza'}</dd>
          {claim && <dd className="text-botanica-grafito">Desde {formatDateTime(claim.claimed_at)}</dd>}
        </div>
      </dl>

      {!card ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-botanica-grafito">
            Genera la clave secreta para la tarjeta rasca. Se muestra <strong>una sola vez</strong> para imprimirla.
          </p>
          <div><button type="button" className="btn-primary btn-publish" disabled={!state.certificate_active} onClick={() => void issue()}>Generar tarjeta</button></div>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-botanica-grafito">¿Qué pasó?</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-secondary" onClick={() => void run('card/replace')}>Perdió la tarjeta</button>
            {card.status === 'active' && !lockedUntil
              ? <button type="button" className="btn-secondary" onClick={() => void run('card/block')}>Le robaron la tarjeta</button>
              : <button type="button" className="btn-secondary" onClick={() => void run('card/unblock')}>Desbloquear tarjeta</button>}
            {claim && <button type="button" className="btn-secondary" onClick={() => void run('claim/release')}>Olvidó su PIN</button>}
            <button type="button" className="btn-secondary" onClick={() => void run('transfer')}>Vendió o regaló la pieza</button>
            {state.reported_stolen_at
              ? <button type="button" className="btn-secondary" onClick={() => void run('stolen/clear')}>Pieza recuperada</button>
              : <button type="button" className="btn-secondary" onClick={() => void run('stolen')}>Robaron la pieza</button>}
          </div>
          <p className="text-xs text-botanica-gris">
            Si se perdió o dañó el <em>chip</em> (no la tarjeta), usa "Reemplazar chip o revocar" arriba.
          </p>
        </div>
      )}
    </section>
  );
}

function Printable({ card, name, onPrinted }: { card: CardKey; name: string; onPrinted: () => void }) {
  return (
    <section className="card-elevated p-5 sm:p-6 flex flex-col gap-4">
      <p role="alert" className="no-print rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-botanica-negro">
        Esta clave se muestra <strong>una sola vez</strong>. Imprímela ahora, prueba el desbloqueo escaneando el chip
        (sin reclamar la pieza) y después cúbrela con la capa rasca.
      </p>
      <div className="print-card mx-auto w-full max-w-sm rounded-2xl border-2 border-botanica-negro p-6 text-center flex flex-col gap-3">
        <p className="font-serif text-2xl text-botanica-negro">ArtesaNFC</p>
        <p className="text-sm text-botanica-grafito">Certificado original de</p>
        <p className="font-serif text-lg text-botanica-negro">{name}</p>
        <p className="text-xs font-mono text-botanica-gris">{card.public_code}</p>
        <div className="my-2 rounded-xl bg-botanica-gris/10 py-4">
          <p className="text-xs uppercase tracking-widest text-botanica-gris">Clave secreta</p>
          <p className="font-mono text-2xl sm:text-3xl tracking-[0.08em] whitespace-nowrap text-botanica-negro">{card.key}</p>
        </div>
        <p className="text-xs text-botanica-grafito">
          Escanea el chip de tu pieza y escribe esta clave. Solo en <strong>artesanfc.com</strong>: nadie de ArtesaNFC te
          la pedirá por mensaje.
        </p>
      </div>
      <div className="no-print flex flex-wrap justify-center gap-2">
        <button type="button" className="btn-primary" onClick={() => window.print()}>Imprimir</button>
        <button type="button" className="btn-secondary" onClick={onPrinted}>Ya la imprimí</button>
      </div>
    </section>
  );
}
