// /autorizacion/#{token} — the artisan (or the relative who helps them) sees
// everything that is published about them and answers (P-026 G3):
// "Sí, autorizo", "Quiero cambios" (with a comment; nothing is unpublished)
// or "No autorizo" (the team's site takes the profile down at once).
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { currentApiConfig, resolveMediaUrl } from "@/lib/api-config";
import type { ArtisanAuthorizationOpen, AuthorizationDecision } from "@/lib/types";
import { LoadingView } from "./StatusView";

type View =
  | { kind: "loading" }
  | { kind: "open"; data: ArtisanAuthorizationOpen }
  | { kind: "unavailable" }
  | { kind: "error" }
  | { kind: "done"; decision: AuthorizationDecision };

type Step = "choose" | "changes" | "decline";

const CONTACT_LABELS: Record<string, string> = {
  telefono: "Teléfono",
  whatsapp: "WhatsApp",
  correo: "Correo",
  email: "Correo",
  instagram: "Instagram",
  facebook: "Facebook",
  sitio: "Sitio web",
};

const DONE: Record<AuthorizationDecision, { title: string; body: string }> = {
  authorize: {
    title: "¡Gracias! Quedó autorizado.",
    body: "El equipo de ArtesaNFC mostrará tu información en artesanfc.com tal como la viste.",
  },
  changes: {
    title: "Gracias, ya recibimos tus cambios.",
    body: "El equipo los corregirá y te mandará un enlace nuevo por WhatsApp para que veas cómo quedó.",
  },
  decline: {
    title: "Entendido. Ya no aparece en artesanfc.com.",
    body: "Retiramos tu información y tus piezas del sitio. Si cambias de opinión, avísale al equipo.",
  },
};

// Read once; the fragment is never sent to any server by the browser.
const token = window.location.hash.replace(/^#/, "");

export default function ArtisanAuthorization() {
  const [view, setView] = useState<View>(token ? { kind: "loading" } : { kind: "unavailable" });
  const [step, setStep] = useState<Step>("choose");
  const [comment, setComment] = useState("");
  const [needsComment, setNeedsComment] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!token) return;
    void api()
      .resolveAuthorization(token)
      .then((r) =>
        setView(
          r.kind === "open"
            ? { kind: "open", data: r.data }
            : r.kind === "unavailable"
              ? { kind: "unavailable" }
              : { kind: "error" },
        ),
      );
  }, []);

  async function decide(decision: AuthorizationDecision) {
    if (decision === "changes" && comment.trim().length < 3) {
      setNeedsComment(true);
      return;
    }
    setBusy(true);
    const result = await api().decideAuthorization(
      token,
      decision,
      decision === "authorize" ? null : comment.trim() || null,
    );
    setBusy(false);
    if (result === "comment_required") {
      setNeedsComment(true);
      return;
    }
    setView(
      result === "recorded"
        ? { kind: "done", decision }
        : result === "unavailable"
          ? { kind: "unavailable" }
          : { kind: "error" },
    );
  }

  if (view.kind === "loading") return <LoadingView label="Abriendo…" />;
  if (view.kind === "unavailable") {
    return (
      <div className="status-view" data-state="auth-unavailable">
        <p className="eyebrow">Autorización</p>
        <h1 className="heading-1">Este enlace ya no está activo.</h1>
        <p className="lead muted">
          Puede que ya hayas respondido o que el enlace haya vencido. Pide uno nuevo al equipo de
          ArtesaNFC.
        </p>
      </div>
    );
  }
  if (view.kind === "error") {
    return (
      <div className="status-view" data-state="auth-error">
        <p className="eyebrow">Autorización</p>
        <h1 className="heading-1">No pudimos abrir la página.</h1>
        <p className="lead muted">Revisa tu conexión y vuelve a abrir el enlace en unos minutos.</p>
      </div>
    );
  }
  if (view.kind === "done") {
    const done = DONE[view.decision];
    return (
      <div className="status-view" data-state={`auth-${view.decision}`}>
        <p className="eyebrow">Autorización</p>
        <h1 className="heading-1">{done.title}</h1>
        <p className="lead muted">{done.body}</p>
      </div>
    );
  }

  const { data } = view;
  const config = currentApiConfig();
  const portrait = resolveMediaUrl(data.portrait, config);
  const name = data.artistic_name || data.full_name;
  const contact = Object.entries(data.public_contact ?? {}).filter(([, v]) => v);
  const techniques = data.techniques ?? [];
  const languages = data.languages ?? [];
  const pieces = data.pieces ?? [];
  return (
    <article className="review" aria-labelledby="auth-title">
      <header className="review__head">
        <p className="eyebrow">Autorización para publicar</p>
        <h1 id="auth-title" className="heading-1">
          {name}, {data.confirming ? "¿nos confirmas tu permiso?" : "¿nos das permiso?"}
        </h1>
        <p className="lead muted">
          Esto es todo lo que aparece de ti en artesanfc.com. Revísalo con calma: si algo no está
          bien, toca «Quiero cambios» y dinos qué corregir.
        </p>
      </header>
      <section className="authorization-card" aria-label="Lo que se publica">
        {portrait && (
          <img className="authorization-card__photo" src={portrait} alt={`Retrato de ${name}`} />
        )}
        <h2 className="heading-2">{data.full_name}</h2>
        {data.artistic_name && <p className="muted">{data.artistic_name}</p>}
        {data.place && <p className="muted">{data.place}</p>}
        {data.biography && (
          <div className="authorization-card__section">
            <h3 className="eyebrow">Sobre ti</h3>
            <p className="authorization-card__bio">{data.biography}</p>
          </div>
        )}
        {data.history && (
          <div className="authorization-card__section">
            <h3 className="eyebrow">Tu historia</h3>
            <p className="authorization-card__bio">{data.history}</p>
          </div>
        )}
        {techniques.length > 0 && (
          <div className="authorization-card__section">
            <h3 className="eyebrow">Técnicas</h3>
            <p>{techniques.join(" · ")}</p>
          </div>
        )}
        {languages.length > 0 && (
          <div className="authorization-card__section">
            <h3 className="eyebrow">Lenguas</h3>
            <p>{languages.join(" · ")}</p>
          </div>
        )}
        {contact.length > 0 && (
          <div className="authorization-card__section" data-testid="auth-contact">
            <h3 className="eyebrow">Contacto que se muestra al público</h3>
            <ul className="authorization-card__list">
              {contact.map(([key, value]) => (
                <li key={key}>
                  {CONTACT_LABELS[key] ?? key}: <strong>{value}</strong>
                </li>
              ))}
            </ul>
          </div>
        )}
        {pieces.length > 0 && (
          <div className="authorization-card__section">
            <h3 className="eyebrow">Tus piezas en el sitio</h3>
            <ul className="authorization-card__pieces">
              {pieces.map((piece) => {
                const cover = resolveMediaUrl(piece.cover, config);
                return (
                  <li key={piece.name}>
                    {cover && <img src={cover} alt="" loading="lazy" />}
                    <span>{piece.name}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </section>

      {step === "choose" && (
        <div className="review__actions">
          <button
            type="button"
            className="button review__approve"
            disabled={busy}
            onClick={() => void decide("authorize")}
          >
            Sí, autorizo
          </button>
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => setStep("changes")}
          >
            Quiero cambios
          </button>
          <button
            type="button"
            className="button button--quiet"
            disabled={busy}
            onClick={() => setStep("decline")}
          >
            No autorizo
          </button>
        </div>
      )}

      {step !== "choose" && (
        <form
          className="review__form"
          onSubmit={(e) => {
            e.preventDefault();
            void decide(step);
          }}
        >
          {step === "decline" && (
            <p className="lead">
              Si no autorizas, retiramos de inmediato tu información y tus piezas de artesanfc.com.
            </p>
          )}
          <label className="field">
            <span>
              {step === "changes"
                ? "¿Qué te gustaría cambiar?"
                : "Si quieres, dinos por qué (no es obligatorio)"}
            </span>
            <textarea
              className="input"
              rows={4}
              maxLength={500}
              value={comment}
              aria-invalid={needsComment || undefined}
              onChange={(e) => {
                setComment(e.target.value);
                setNeedsComment(false);
              }}
            />
          </label>
          {needsComment && (
            <p role="alert" className="muted">
              Escribe qué te gustaría cambiar.
            </p>
          )}
          <div className="review__actions">
            <button type="submit" className="button review__approve" disabled={busy}>
              {step === "changes" ? "Enviar cambios" : "No autorizo, retirar mi información"}
            </button>
            <button
              type="button"
              className="button button--quiet"
              disabled={busy}
              onClick={() => {
                setStep("choose");
                setNeedsComment(false);
              }}
            >
              Volver
            </button>
          </div>
        </form>
      )}
    </article>
  );
}
