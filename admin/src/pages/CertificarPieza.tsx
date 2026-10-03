import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError, adminApi } from '../api';
import type { CustodyIssued, CustodyState } from '../api';
import { formatDateTime, labels, writeErrorMessage } from '../format';
import { useConfirm, useToast } from '../feedback-context';
import { useLoad } from '../hooks';
import { NfcError, lockTag, nfcSupported, readTag, writeUrl } from '../nfc';
import { Badge, ErrorState, Loading } from '../ui';

// ADR-030 phase 2: certify a piece and write its NFC tag from Gestión
// (Chrome for Android, Web NFC). Order: read the chip -> the server issues the
// certificate (token generated BEFORE writing) -> write the URL -> read it back
// and compare UID and URL -> record "programmed" -> optional lock, last.
// The certificate URL lives only in this component's memory.

const PRODUCTION_PREFIX = 'https://artesanfc.com/c/';

const REASONS: Record<string, string> = {
  lost: 'Chip perdido',
  damaged: 'Chip dañado',
  compromised: 'Enlace comprometido',
  'wrong-tag': 'Chip equivocado',
  replaced: 'Reemplazo de chip',
  'not-deployed': 'Nunca se instaló',
  other: 'Otro motivo',
};

const BLOCKERS: Record<string, string> = {
  piece_not_published: 'La pieza no está publicada.',
  artisan_not_published: 'El artesano no está publicado.',
  active_certificate_exists: 'La pieza ya tiene un certificado activo.',
  tag_in_use: 'La pieza ya tiene un chip asignado.',
  no_active_certificate: 'La pieza no tiene certificado activo.',
  tag_already_locked: 'El chip ya está bloqueado.',
  no_programmed_tag: 'No hay un chip grabado que bloquear.',
  uid_mismatch: 'El chip leído no es el registrado. ¿Acercaste otro chip? Si se grabó otro chip, retíralo o reemplaza el certificado.',
  uid_already_registered: 'Ese chip ya está registrado en otra pieza.',
  uid_not_nxp: 'Ese chip no es NTAG (su número no empieza con 04).',
  uid_wrong_length: 'El número del chip no tiene el largo esperado.',
  self_check_failed: 'El certificado no se pudo verificar. Vuelve a emitirlo.',
  tag_not_available: 'El estado del chip cambió. Recarga la página.',
  tag_not_found: 'Ese chip no pertenece a esta pieza. Recarga la página.',
  stale: 'Alguien más cambió esta pieza. Recarga la página.',
  invalid_transition: 'Ese paso ya no aplica al estado actual. Recarga la página.',
};

type Phase =
  | { step: 'idle' }
  | { step: 'reading'; purpose: 'issue' | 'replace' }
  | { step: 'confirm'; purpose: 'issue' | 'replace'; uid: string }
  | { step: 'writing'; issued: CustodyIssued }
  | { step: 'verifying'; issued: CustodyIssued }
  | { step: 'locking' };

function message(e: unknown): string {
  if (e instanceof NfcError) return e.message;
  if (e instanceof ApiError && e.code && BLOCKERS[e.code]) return BLOCKERS[e.code];
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

export default function CertificarPieza() {
  const { id = '' } = useParams<{ id: string }>();
  const [revision, setRevision] = useState(0);
  // Lives here, not in the wizard, so it survives any reload.
  const [error, setError] = useState<string | null>(null);
  const state = useLoad(`custody:${id}:${revision}`, (signal) => adminApi.custodyState(id, signal));
  // Keep the wizard mounted while it reloads: a write that failed keeps the
  // issued URL in its state so it can be retried without reissuing.
  const [shown, setShown] = useState<CustodyState | null>(null);
  if (state.status === 'ready' && state.data !== shown) setShown(state.data);
  return (
    <div className="max-w-3xl mx-auto pb-12">
      <Link to="/certificacion" className="inline-flex items-center text-sm font-medium text-botanica-grafito hover:text-botanica-negro mb-8">
        ← Certificación
      </Link>
      {state.status === 'loading' && !shown && <Loading label="Cargando..." />}
      {state.status === 'error' && <div className="card-elevated"><ErrorState error={state.error} /></div>}
      {state.status !== 'error' && shown && (
        <Wizard key={shown.piece_id} state={shown} reload={() => setRevision((r) => r + 1)} error={error} setError={setError} />
      )}
    </div>
  );
}

interface WizardProps {
  state: CustodyState;
  reload: () => void;
  error: string | null;
  setError: (message: string | null) => void;
}

function Wizard({ state, reload, error, setError }: WizardProps) {
  const [phase, setPhase] = useState<Phase>({ step: 'idle' });
  const confirm = useConfirm();
  const toast = useToast();
  const supported = nfcSupported();
  const live = state.tags.find((t) => t.status !== 'available') ?? state.tags[0];

  async function guardRehearsal(url: string): Promise<boolean> {
    if (url.startsWith(PRODUCTION_PREFIX)) return true;
    const answer = await confirm({
      title: 'Este servidor no es producción',
      body: 'La URL generada es de ensayo y no funcionaría en una pieza real. Grábala solo en un chip de prueba.',
      confirmLabel: 'Es un chip de prueba',
      tone: 'danger',
    });
    return answer !== null;
  }

  async function start(purpose: 'issue' | 'replace') {
    setError(null);
    setPhase({ step: 'reading', purpose });
    try {
      const tag = await readTag();
      setPhase({ step: 'confirm', purpose, uid: tag.uid });
    } catch (e) {
      setError(message(e));
      setPhase({ step: 'idle' });
    }
  }

  async function emit(purpose: 'issue' | 'replace' | 'reissue', uid: string | null, reason?: string) {
    setError(null);
    try {
      const issued = purpose === 'issue'
        ? await adminApi.custodyIssue(state.piece_id, uid ?? '')
        : await adminApi.custodyRotate(state.piece_id, reason ?? 'other', uid);
      if (!(await guardRehearsal(issued.url))) {
        setError('Grabación cancelada. El certificado ya se emitió: vuelve a emitirlo cuando tengas el chip correcto.');
        setPhase({ step: 'idle' });
        reload();
        return;
      }
      await write(issued);
    } catch (e) {
      setError(message(e));
      setPhase({ step: 'idle' });
      reload();
    }
  }

  async function write(issued: CustodyIssued) {
    setPhase({ step: 'writing', issued });
    try {
      await writeUrl(issued.url);
      setPhase({ step: 'verifying', issued });
      const back = await readTag();
      if (back.url !== issued.url) throw new NfcError('write', 'La lectura de vuelta no coincide con lo grabado. Vuelve a grabar.');
      await adminApi.custodyProgram(state.piece_id, issued.tag_id, back.uid);
      toast('Chip grabado y verificado ✓');
      setPhase({ step: 'idle' });
      reload();
    } catch (e) {
      setError(message(e));
      // Keep the URL in memory so the write can be retried without reissuing.
      setPhase({ step: 'writing', issued });
      reload();
    }
  }

  async function lock() {
    const answer = await confirm({
      title: '¿Bloquear el chip para siempre?',
      body: 'Ya no se podrá volver a grabar. Hazlo solo con la pieza terminada y el chip escaneado con éxito.',
      confirmLabel: 'Bloquear',
      tone: 'danger',
    });
    if (answer === null) return;
    setError(null);
    setPhase({ step: 'locking' });
    try {
      await lockTag();
      const back = await readTag();
      await adminApi.custodyLock(state.piece_id, back.uid);
      toast('Chip bloqueado ✓');
      reload();
    } catch (e) {
      setError(message(e));
    }
    setPhase({ step: 'idle' });
  }

  async function revoke(reason: string) {
    const answer = await confirm({
      title: '¿Revocar el certificado?',
      body: 'El chip dejará de autenticar de inmediato y se retirará. No se puede deshacer; después se puede emitir uno nuevo.',
      confirmLabel: 'Revocar',
      tone: 'danger',
    });
    if (answer === null) return;
    try {
      await adminApi.custodyRevoke(state.piece_id, reason);
      toast('Certificado revocado');
      reload();
    } catch (e) {
      setError(message(e));
    }
  }

  const blockers = state.certificate_active ? [] : state.issue_blockers;

  return (
    <div className="flex flex-col gap-6">
      <header className="card-elevated p-5 sm:p-6 flex flex-col gap-2">
        <p className="text-xs font-mono text-botanica-gris">{state.public_code}</p>
        <h1 className="text-3xl font-serif text-botanica-negro">{state.name}</h1>
        <p className="text-botanica-grafito">{state.artisan_name}</p>
        <div className="flex flex-wrap gap-2 mt-2">
          <Badge tone={state.certificate_active ? 'jade' : 'neutral'}>
            {state.certificate_active ? `Certificado activo · ${formatDateTime(state.certificate_issued_at)}` : 'Sin certificado activo'}
          </Badge>
          {live && <Badge tone={live.status === 'locked' ? 'jade' : 'neutral'}>Chip {labels.nfc(live.status)} · {live.uid}</Badge>}
          {state.revoked_certificates > 0 && <Badge tone="lavanda">{state.revoked_certificates} revocado(s)</Badge>}
        </div>
      </header>

      {!supported && (
        <p role="alert" className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-botanica-negro">
          Para grabar chips abre esta página en <strong>Chrome para Android</strong> con NFC activado. Desde aquí solo puedes ver el estado.
        </p>
      )}
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}

      <section className="card-elevated p-5 sm:p-6 flex flex-col gap-4" aria-live="polite">
        {phase.step === 'reading' && <Step title="Acerca el chip al teléfono" body="Sostén el chip en la parte trasera del teléfono hasta que se lea." pulse />}
        {phase.step === 'confirm' && (
          <>
            <Step title="Chip detectado" body={`Número del chip: ${phase.uid}`} />
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary btn-publish"
                onClick={() => void emit(phase.purpose === 'issue' ? 'issue' : 'replace', phase.uid, phase.purpose === 'replace' ? 'replaced' : undefined)}>
                {phase.purpose === 'issue' ? 'Emitir certificado y grabar' : 'Reemplazar y grabar'}
              </button>
              <button type="button" className="btn-secondary" onClick={() => setPhase({ step: 'idle' })}>Cancelar</button>
            </div>
          </>
        )}
        {phase.step === 'writing' && (
          <>
            <Step title="Grabando…" body="Mantén el chip junto al teléfono. Si falló, vuelve a acercarlo y pulsa Reintentar." pulse={!error} />
            {error && <button type="button" className="btn-primary" onClick={() => void write(phase.issued)}>Reintentar grabación</button>}
          </>
        )}
        {phase.step === 'verifying' && <Step title="Verificando…" body="Leyendo de vuelta para comprobar lo grabado." pulse />}
        {phase.step === 'locking' && <Step title="Bloqueando…" body="Mantén el chip junto al teléfono." pulse />}

        {phase.step === 'idle' && (
          <>
            {state.recommended_action === 'issue' && (
              blockers.length > 0 ? (
                <Step title="Aún no se puede certificar" body={blockers.map((b) => BLOCKERS[b] ?? b).join(' ')} />
              ) : (
                <>
                  <Step title="1. Certificar y grabar el chip" body="Se leerá el chip, se emitirá el certificado y se grabará su enlace. El enlace nunca se muestra." />
                  <div><button type="button" className="btn-primary btn-publish" disabled={!supported} onClick={() => void start('issue')}>Empezar</button></div>
                </>
              )
            )}
            {state.recommended_action === 'interrupted_rotate' && (
              <>
                <Step title="La grabación quedó a medias" body="El certificado se emitió pero el chip no se confirmó. Vuelve a emitir el enlace y grábalo en el mismo chip." />
                <div><button type="button" className="btn-primary" disabled={!supported} onClick={() => void emit('reissue', null, 'not-deployed')}>Volver a emitir y grabar</button></div>
              </>
            )}
            {state.recommended_action === 'verify_then_optional_lock' && (
              <>
                <Step title="Chip grabado ✓" body="Escanéalo con otro teléfono: debe abrir el certificado de esta pieza. Cuando la pieza esté terminada puedes bloquearlo (opcional, irreversible)." />
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-secondary" disabled={!supported} onClick={() => void lock()}>Bloquear chip</button>
                  <button type="button" className="btn-secondary" disabled={!supported} onClick={() => void emit('reissue', null, 'other')}>Regrabar el mismo chip</button>
                </div>
              </>
            )}
            {state.recommended_action === 'locked' && <Step title="Certificada y bloqueada ✓" body="El chip está grabado y protegido contra reescritura." />}
            {state.recommended_action === 'revoked_with_tags' && (
              <Step title="Requiere revisión manual" body="La pieza conserva un chip vigente sin certificado activo. Resuélvelo con la CLI de aprovisionamiento (docs/PROVISIONING.md) antes de volver a certificar." />
            )}

            {state.certificate_active && (
              <details className="rounded-lg border border-botanica-gris/20 p-4">
                <summary className="cursor-pointer text-sm font-medium text-botanica-negro">Reemplazar chip o revocar</summary>
                <div className="mt-4 flex flex-col gap-3 text-sm">
                  <button type="button" className="btn-secondary self-start" disabled={!supported} onClick={() => void start('replace')}>
                    Reemplazar por un chip nuevo
                  </button>
                  <div className="flex flex-wrap items-center gap-2">
                    <span>Revocar por:</span>
                    {state.revocation_reasons.map((r) => (
                      <button key={r} type="button" className="btn-secondary !py-1.5 !px-3 text-xs" onClick={() => void revoke(r)}>{REASONS[r] ?? r}</button>
                    ))}
                  </div>
                </div>
              </details>
            )}
          </>
        )}
      </section>
    </div>
  );
}

function Step({ title, body, pulse = false }: { title: string; body: string; pulse?: boolean }) {
  return (
    <div className="flex items-start gap-3">
      <span aria-hidden="true" className={`nfc-dot ${pulse ? 'nfc-dot--pulse' : ''}`} />
      <div>
        <h2 className="text-lg font-serif text-botanica-negro">{title}</h2>
        <p className="text-sm text-botanica-grafito">{body}</p>
      </div>
    </div>
  );
}
