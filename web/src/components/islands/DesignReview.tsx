// /revision/#{token} — the artisan's review of a certificate design
// (ADR-030 phase 5): see it, then approve or ask for changes. Written for
// someone opening a WhatsApp link: big image, two big buttons, plain words.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DesignReviewOpen } from "@/lib/types";
import { LoadingView } from "./StatusView";

type View =
  | { kind: "loading" }
  | { kind: "open"; data: DesignReviewOpen }
  | { kind: "unavailable" }
  | { kind: "error" }
  | { kind: "done"; approved: boolean };

// Read once; the fragment is never sent to any server by the browser.
const token = window.location.hash.replace(/^#/, "");

const svgSrc = (svg: string) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;

export default function DesignReview() {
  const [view, setView] = useState<View>(token ? { kind: "loading" } : { kind: "unavailable" });
  const [asking, setAsking] = useState(false);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!token) return;
    void api()
      .resolveReview(token)
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

  async function decide(approve: boolean) {
    setBusy(true);
    const result = await api().decideReview(
      token,
      approve ? "approve" : "changes",
      approve ? null : comment.trim() || null,
    );
    setBusy(false);
    setView(
      result === "recorded"
        ? { kind: "done", approved: approve }
        : result === "unavailable"
          ? { kind: "unavailable" }
          : { kind: "error" },
    );
  }

  if (view.kind === "loading") return <LoadingView label="Abriendo el diseño…" />;
  if (view.kind === "unavailable") {
    return (
      <div className="status-view" data-state="review-unavailable">
        <p className="eyebrow">Revisión del certificado</p>
        <h1 className="heading-1">Este enlace ya no está activo.</h1>
        <p className="lead muted">
          Puede que ya hayas respondido, que el diseño haya cambiado o que el enlace haya caducado.
          Pide uno nuevo al equipo de ArtesaNFC.
        </p>
      </div>
    );
  }
  if (view.kind === "error") {
    return (
      <div className="status-view" data-state="review-error">
        <p className="eyebrow">Revisión del certificado</p>
        <h1 className="heading-1">No pudimos abrir el diseño.</h1>
        <p className="lead muted">Revisa tu conexión y vuelve a abrir el enlace en unos minutos.</p>
      </div>
    );
  }
  if (view.kind === "done") {
    return (
      <div
        className="status-view"
        data-state={view.approved ? "review-approved" : "review-changes"}
      >
        <p className="eyebrow">Revisión del certificado</p>
        <h1 className="heading-1">
          {view.approved ? "¡Gracias! Quedó aprobado." : "Gracias, le avisamos al equipo."}
        </h1>
        <p className="lead muted">
          {view.approved
            ? "El equipo de ArtesaNFC lo publicará: es el certificado que verá quien tenga tu pieza."
            : "Harán los cambios y te mandarán un enlace nuevo para que lo revises."}
        </p>
      </div>
    );
  }

  const { data } = view;
  return (
    <article className="review" aria-labelledby="review-title">
      <header className="review__head">
        <p className="eyebrow">Revisión del certificado · versión {data.version}</p>
        <h1 id="review-title" className="heading-1">
          {data.piece_name}
        </h1>
        <p className="lead muted">
          {data.artisan_name ? `${data.artisan_name}, este` : "Este"} es el certificado que verá
          quien tenga tu pieza. ¿Lo apruebas?
        </p>
      </header>
      <img
        className="review__image"
        src={svgSrc(data.svg)}
        alt={`Diseño del certificado de ${data.piece_name}`}
      />
      {asking ? (
        <form
          className="review__form"
          onSubmit={(e) => {
            e.preventDefault();
            void decide(false);
          }}
        >
          <label className="field">
            <span>¿Qué te gustaría cambiar?</span>
            <textarea
              className="input"
              rows={4}
              maxLength={500}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          </label>
          <div className="review__actions">
            <button type="submit" className="button" disabled={busy}>
              Enviar cambios
            </button>
            <button type="button" className="button button--quiet" onClick={() => setAsking(false)}>
              Volver
            </button>
          </div>
        </form>
      ) : (
        <div className="review__actions">
          <button
            type="button"
            className="button review__approve"
            disabled={busy}
            onClick={() => void decide(true)}
          >
            Sí, lo apruebo
          </button>
          <button
            type="button"
            className="button button--quiet"
            disabled={busy}
            onClick={() => setAsking(true)}
          >
            Quiero cambios
          </button>
        </div>
      )}
    </article>
  );
}
