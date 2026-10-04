// ADR-030 phase 3: the original certificate behind the buyer's scratch card.
//
// The chip's token (from the page) + the card key (+ the PIN once the piece is
// claimed) open the original. The key, PIN and email only live in this
// component's state and the POST body: never rendered back, stored, logged or
// put in a URL. Every refusal reads the same ("invalid" never says why).
import { useState, type FormEvent } from "react";
import { api, type UnlockResult } from "@/lib/api";
import { displayName } from "@/lib/format";
import type { CertificateOriginal } from "@/lib/types";

type Step = { kind: "key" } | { kind: "pin" } | { kind: "open"; data: CertificateOriginal };

const DATE = new Intl.DateTimeFormat("es-MX", { dateStyle: "long" });
const date = (iso: string | null) => (iso ? DATE.format(new Date(iso)) : "");

function refusalMessage(result: UnlockResult): string | null {
  switch (result.kind) {
    case "refused":
      if (result.result === "reported_stolen")
        return "Esta pieza fue reportada como robada. Si eres su dueño, contacta a ArtesaNFC.";
      if (result.result === "invalid")
        return "No pudimos abrir el certificado con esos datos. Revisa la clave (y el PIN, si lo pide) e inténtalo de nuevo.";
      return null;
    case "locked": {
      const minutes = result.retryAfter ? Math.max(1, Math.ceil(result.retryAfter / 60)) : null;
      return minutes
        ? `Demasiados intentos. Por seguridad, espera ${minutes} min antes de volver a intentarlo.`
        : "Demasiados intentos. Espera unos minutos antes de volver a intentarlo.";
    }
    case "rejected":
      return (
        {
          invalid_email: "Escribe un correo válido.",
          invalid_pin: "El PIN debe tener 6 números.",
          weak_pin: "Ese PIN es muy fácil de adivinar. Elige otro.",
        }[result.code] ?? "Revisa los datos e inténtalo de nuevo."
      );
    case "error":
      return "El servicio no respondió. Vuelve a intentarlo en unos minutos.";
    default:
      return null;
  }
}

export function OriginalCertificate({ token }: { token: string }) {
  const [step, setStep] = useState<Step>({ kind: "key" });
  const [key, setKey] = useState("");
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handle(result: UnlockResult) {
    if (result.kind === "unlocked") {
      setStep({ kind: "open", data: result.data });
      setPin("");
      setError(null);
    } else if (result.kind === "refused" && result.result === "pin_required") {
      setStep({ kind: "pin" });
      setError(step.kind === "pin" ? refusalMessage({ kind: "refused", result: "invalid" }) : null);
    } else {
      setError(refusalMessage(result));
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    await handle(await api().unlockCertificate(token, key, step.kind === "pin" ? pin : null));
    setBusy(false);
  }

  if (step.kind === "open") {
    return (
      <OriginalView
        data={step.data}
        token={token}
        cardKey={key}
        onClaimed={(data) => setStep({ kind: "open", data })}
      />
    );
  }

  return (
    <section
      className="original original--locked"
      aria-labelledby="original-title"
      data-state="original-locked"
    >
      <p className="eyebrow">Para el dueño de la pieza</p>
      <h2 id="original-title" className="heading-2">
        Certificado original
      </h2>
      <p className="muted">
        Raspa tu tarjeta y escribe la clave. Solo escríbela aquí, en <strong>artesanfc.com</strong>:
        nadie de ArtesaNFC te la pedirá por mensaje.
      </p>
      <form className="original__form" onSubmit={(e) => void submit(e)}>
        <label className="field">
          <span>Clave de la tarjeta</span>
          <input
            className="input input--key"
            value={key}
            onChange={(e) => setKey(e.target.value.toUpperCase())}
            placeholder="XXXX-XXXX-XX"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={16}
            required
            disabled={step.kind === "pin"}
          />
        </label>
        {step.kind === "pin" && (
          <label className="field">
            <span>Tu PIN de 6 números</span>
            <input
              className="input"
              value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
              inputMode="numeric"
              autoComplete="off"
              pattern="[0-9]{6}"
              required
            />
            <small className="muted">
              Esta pieza ya tiene dueño registrado: además de la tarjeta se pide su PIN.
            </small>
          </label>
        )}
        {error && (
          <p className="original__error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="button" disabled={busy}>
          {busy ? "Verificando…" : "Ver certificado original"}
        </button>
      </form>
    </section>
  );
}

function OriginalView({
  data,
  token,
  cardKey,
  onClaimed,
}: {
  data: CertificateOriginal;
  token: string;
  cardKey: string;
  onClaimed: (data: CertificateOriginal) => void;
}) {
  const { piece, artisan, authenticity, ownership } = data;
  return (
    <section
      className="original original--open"
      aria-labelledby="original-open-title"
      data-state="original-open"
    >
      {data.design && (
        <img
          className="original__design"
          src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(data.design.svg)}`}
          alt={`Certificado original de ${piece.name}, versión ${data.design.version} del diseño`}
          data-state="original-design"
        />
      )}
      <div className="original__card">
        <p className="certificate__seal">
          <span aria-hidden="true">✓</span> Certificado original
        </p>
        <h2 id="original-open-title" className="heading-2">
          {piece.name}
        </h2>
        <p className="muted">Creada por {displayName(artisan)}</p>
        <dl className="original__facts">
          <div>
            <dt>Código de la pieza</dt>
            <dd>{piece.public_code}</dd>
          </div>
          <div>
            <dt>Certificado</dt>
            <dd>
              Versión {authenticity.certificate_version} · {date(authenticity.issued_at)}
            </dd>
          </div>
          <div>
            <dt>Tarjeta del dueño</dt>
            <dd>Emitida el {date(ownership.card_issued_at)}</dd>
          </div>
          <div>
            <dt>Dueño</dt>
            <dd>
              {ownership.claimed
                ? `Registrado (${ownership.owner_email_masked}) desde el ${date(ownership.claimed_at)}`
                : "Sin registrar"}
            </dd>
          </div>
        </dl>
      </div>
      {!ownership.claimed && <ClaimForm token={token} cardKey={cardKey} onClaimed={onClaimed} />}
    </section>
  );
}

function ClaimForm({
  token,
  cardKey,
  onClaimed,
}: {
  token: string;
  cardKey: string;
  onClaimed: (data: CertificateOriginal) => void;
}) {
  const [email, setEmail] = useState("");
  const [pin, setPin] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pin !== again) {
      setError("Los dos PIN no coinciden.");
      return;
    }
    setBusy(true);
    const result = await api().claimPiece(token, cardKey, email, pin);
    setBusy(false);
    if (result.kind === "unlocked") onClaimed(result.data);
    else if (result.kind === "refused" && result.result === "pin_required")
      setError(
        "Esta pieza acaba de ser registrada por otra persona. Si eres su dueño, contacta a ArtesaNFC.",
      );
    else setError(refusalMessage(result));
  }

  return (
    <form
      className="original__form original__claim"
      onSubmit={(e) => void submit(e)}
      aria-labelledby="claim-title"
    >
      <h3 id="claim-title">Registra tu pieza</h3>
      <p className="muted">
        Si es tuya, regístrala con tu correo y un PIN. Desde entonces, para abrir este certificado
        se pedirán tu tarjeta <strong>y</strong> tu PIN: si alguien encuentra la tarjeta, no le
        servirá sola. Tu correo nos permite ayudarte si pierdes la tarjeta o vendes la pieza.
      </p>
      <p className="original__note muted">¿Eres del equipo probando la tarjeta? No la registres.</p>
      <label className="field">
        <span>Correo</span>
        <input
          className="input"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          required
        />
      </label>
      <label className="field">
        <span>PIN de 6 números</span>
        <input
          className="input"
          value={pin}
          onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
          inputMode="numeric"
          autoComplete="new-password"
          pattern="[0-9]{6}"
          required
        />
      </label>
      <label className="field">
        <span>Repite el PIN</span>
        <input
          className="input"
          value={again}
          onChange={(e) => setAgain(e.target.value.replace(/\D/g, "").slice(0, 6))}
          inputMode="numeric"
          autoComplete="new-password"
          pattern="[0-9]{6}"
          required
        />
      </label>
      {error && (
        <p className="original__error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="button" disabled={busy}>
        {busy ? "Registrando…" : "Registrar mi pieza"}
      </button>
    </form>
  );
}
