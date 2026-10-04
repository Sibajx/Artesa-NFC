// /autorizacion/#{token} — the artisan (or the relative who helps them) sees
// exactly what will be published and answers with one tap (P-026 G3).
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { currentApiConfig, resolveMediaUrl } from "@/lib/api-config";
import type { ArtisanAuthorizationOpen } from "@/lib/types";
import { LoadingView } from "./StatusView";

type View =
  | { kind: "loading" }
  | { kind: "open"; data: ArtisanAuthorizationOpen }
  | { kind: "unavailable" }
  | { kind: "error" }
  | { kind: "done"; authorized: boolean };

// Read once; the fragment is never sent to any server by the browser.
const token = window.location.hash.replace(/^#/, "");

export default function ArtisanAuthorization() {
  const [view, setView] = useState<View>(token ? { kind: "loading" } : { kind: "unavailable" });
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

  async function decide(authorize: boolean) {
    setBusy(true);
    const result = await api().decideAuthorization(
      token,
      authorize ? "authorize" : "decline",
      null,
    );
    setBusy(false);
    setView(
      result === "recorded"
        ? { kind: "done", authorized: authorize }
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
    return (
      <div
        className="status-view"
        data-state={view.authorized ? "auth-authorized" : "auth-declined"}
      >
        <p className="eyebrow">Autorización</p>
        <h1 className="heading-1">
          {view.authorized ? "¡Gracias! Quedó autorizado." : "Entendido, no se publicará."}
        </h1>
        <p className="lead muted">
          {view.authorized
            ? "El equipo de ArtesaNFC publicará tu información en artesanfc.com."
            : "Nada se publica sin tu permiso. Si cambias de opinión, avísale al equipo."}
        </p>
      </div>
    );
  }

  const { data } = view;
  const portrait = resolveMediaUrl(data.portrait, currentApiConfig());
  const name = data.artistic_name || data.full_name;
  return (
    <article className="review" aria-labelledby="auth-title">
      <header className="review__head">
        <p className="eyebrow">Autorización para publicar</p>
        <h1 id="auth-title" className="heading-1">
          {name}, ¿nos das permiso?
        </h1>
        <p className="lead muted">
          Así aparecerán tu nombre, tu foto y tu historia en artesanfc.com:
        </p>
      </header>
      <section className="authorization-card" aria-label="Lo que se publicará">
        {portrait && (
          <img className="authorization-card__photo" src={portrait} alt={`Retrato de ${name}`} />
        )}
        <h2 className="heading-2">{data.full_name}</h2>
        {data.artistic_name && <p className="muted">{data.artistic_name}</p>}
        {data.place && <p className="muted">{data.place}</p>}
        {data.biography && <p className="authorization-card__bio">{data.biography}</p>}
      </section>
      <div className="review__actions">
        <button
          type="button"
          className="button review__approve"
          disabled={busy}
          onClick={() => void decide(true)}
        >
          Sí, autorizo
        </button>
        <button
          type="button"
          className="button button--quiet"
          disabled={busy}
          onClick={() => void decide(false)}
        >
          No autorizo
        </button>
      </div>
    </article>
  );
}
