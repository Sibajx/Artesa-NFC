// /c/{token} — private certificate route (Issue #72, SECURITY.md §4).
//
// Four outcomes, never merged:
//   authentic    → "Certificado válido" + digital passport
//   unavailable  → the API's single public answer for unknown, revoked,
//                  malformed or otherwise unusable tokens. The contract
//                  forbids telling these apart (API_CONTRACT.md §7, §13), so
//                  the page never claims "revoked" or "fake".
//   error        → transport/server failure (5xx, 429, timeout, network):
//                  "verification temporarily unavailable", retryable.
//   bad route    → no API call at all; shown as "unavailable".
//
// Privacy: the token is read only from location.pathname, lives only in this
// module's closure and the POST body, and is never rendered, stored, logged
// or put in a URL. The page is noindex + no-referrer (meta and _headers).
import { useEffect, useState } from "react";
import { api, type UnavailableReason } from "@/lib/api";
import { displayName } from "@/lib/format";
import { pickHeroImage } from "@/lib/media";
import { artisanPath, piecePath, tokenFromPath } from "@/lib/routes";
import type { CertificateAuthentic } from "@/lib/types";
import { MediaImage } from "./MediaImage";
import { OriginalCertificate } from "./OriginalCertificate";
import { PassportPanel } from "./PassportPanel";
import { LoadingView } from "./StatusView";

type View =
  | { kind: "loading" }
  | { kind: "authentic"; data: CertificateAuthentic }
  | { kind: "unavailable" }
  | { kind: "error"; reason: UnavailableReason };

// Read once at module load; never re-read from anywhere else.
const token = tokenFromPath(window.location.pathname);

export default function CertificateView() {
  const [view, setView] = useState<View>(token ? { kind: "loading" } : { kind: "unavailable" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!token) return;
    let active = true;
    void api()
      .resolveCertificate(token)
      .then((result) => {
        if (active) setView(result);
      });
    return () => {
      active = false;
    };
  }, [attempt]);

  if (view.kind === "loading") return <LoadingView label="Verificando certificado…" />;

  if (view.kind === "unavailable") {
    return (
      <div className="status-view certificate-state" data-state="cert-unavailable">
        <p className="eyebrow">Certificado de autenticidad</p>
        <h1 className="heading-1">No podemos confirmar este certificado.</h1>
        <p className="lead muted">
          El enlace puede estar incompleto, el certificado puede no estar vigente o la etiqueta no
          corresponde a un registro activo. Si crees que es un error, contacta al artesano o al
          equipo de ArtesaNFC.
        </p>
        <a className="editorial-link" href="/piezas/">
          Explorar la colección pública
        </a>
      </div>
    );
  }

  if (view.kind === "error") {
    return (
      <div
        className="status-view certificate-state"
        data-state="cert-error"
        data-reason={view.reason}
      >
        <p className="eyebrow">Certificado de autenticidad</p>
        <h1 className="heading-1">Verificación no disponible por ahora.</h1>
        <p className="lead muted">
          {view.reason === "rate_limited"
            ? "Recibimos demasiados intentos seguidos. Espera unos segundos y vuelve a intentarlo."
            : "El servicio de verificación no respondió. Esto no indica nada sobre la pieza: vuelve a intentarlo en unos minutos."}
        </p>
        {view.reason !== "unconfigured" && (
          <button
            type="button"
            className="button"
            onClick={() => {
              setView({ kind: "loading" });
              setAttempt((n) => n + 1);
            }}
          >
            Reintentar
          </button>
        )}
      </div>
    );
  }

  const { piece, artisan } = view.data;
  const stolen = view.data.authenticity.reported_stolen === true;
  const hero = pickHeroImage(piece.media);
  return (
    <article className="certificate" data-state="cert-authentic" aria-labelledby="cert-piece-title">
      <div className="certificate__intro">
        <div className="certificate__media media-frame">
          <MediaImage media={hero} fallbackAlt={piece.name} loading="eager" />
        </div>
        <div className="certificate__heading">
          <p className="certificate__seal">
            <span aria-hidden="true">✓</span> Pieza auténtica
          </p>
          <h1 id="cert-piece-title" className="display-l">
            {piece.name}
          </h1>
          <p className="muted">Creada por {displayName(artisan)}</p>
          {piece.description && <p className="lead">{piece.description}</p>}
        </div>
      </div>
      {stolen && (
        <p className="certificate__alert" role="alert" data-state="cert-stolen">
          <strong>Pieza reportada como robada.</strong> Es auténtica, pero su dueño la reportó como
          robada. Si te la ofrecen en venta, contacta a ArtesaNFC.
        </p>
      )}
      <PassportPanel piece={piece} artisan={artisan} certificate={view.data} />
      {!stolen && token && <OriginalCertificate token={token} />}
      <nav className="certificate__links" aria-label="Más sobre esta pieza">
        <a className="editorial-link" href={piecePath(piece.slug)}>
          Ver la pieza
        </a>
        <a className="editorial-link" href={artisanPath(artisan.slug)}>
          Conocer al artesano
        </a>
      </nav>
    </article>
  );
}
