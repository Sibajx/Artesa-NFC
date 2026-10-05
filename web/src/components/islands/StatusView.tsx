// The non-content states shared by every API-driven view. "not_found" and
// "unavailable" are deliberately different: one is an authoritative answer,
// the other is "could not find out right now" and can be retried.
import { useEffect, useRef } from "react";
import type { UnavailableReason } from "@/lib/api";

interface LoadingProps {
  label: string;
}

export function LoadingView({ label }: LoadingProps) {
  return (
    <div className="status-view" role="status" aria-live="polite" data-state="loading">
      <span className="status-view__pulse" aria-hidden="true" />
      <p className="lead muted">{label}</p>
    </div>
  );
}

interface NotFoundProps {
  title: string;
  body: string;
  backHref: string;
  backLabel: string;
}

export function NotFoundView({ title, body, backHref, backLabel }: NotFoundProps) {
  const heading = useFocusOnMount<HTMLHeadingElement>();
  return (
    <div className="status-view" data-state="not-found">
      <p className="eyebrow">No disponible</p>
      <h1 className="heading-1" ref={heading} tabIndex={-1}>
        {title}
      </h1>
      <p className="lead muted">{body}</p>
      <a className="editorial-link" href={backHref}>
        {backLabel}
      </a>
    </div>
  );
}

interface UnavailableProps {
  what: string;
  reason: UnavailableReason;
  onRetry: () => void;
  /** h1 on detail shells (the state is the page); h2 inside a titled list page. */
  level?: 1 | 2;
}

const REASON_COPY: Record<UnavailableReason, string> = {
  unconfigured: "Este sitio no tiene acceso al servicio de datos desde esta dirección.",
  timeout: "El servicio tardó demasiado en responder.",
  network: "No hay conexión con el servicio en este momento.",
  rate_limited: "Recibimos demasiadas solicitudes seguidas. Espera unos segundos.",
  server_error: "El servicio tuvo un problema temporal.",
  http_error: "El servicio no pudo atender la solicitud.",
  malformed: "El servicio respondió con datos inesperados.",
};

// When the server is down (a power or internet cut at the office), a visitor
// who leaves the page open sees it come back on its own: one retry every
// 30 s, only while the tab is visible, at most AUTO_RETRY_LIMIT per page load.
export const AUTO_RETRY_MS = 30_000;
const AUTO_RETRY_LIMIT = 20;
let autoRetries = 0;

export function retryable(reason: UnavailableReason): boolean {
  return reason !== "unconfigured" && reason !== "rate_limited";
}

export function AutoRetry({ onRetry }: { onRetry: () => void }) {
  useEffect(() => {
    if (autoRetries >= AUTO_RETRY_LIMIT) return;
    const id = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      window.clearInterval(id);
      autoRetries += 1;
      onRetry();
    }, AUTO_RETRY_MS);
    return () => window.clearInterval(id);
  }, [onRetry]);
  if (autoRetries >= AUTO_RETRY_LIMIT) return null;
  return (
    <p className="muted status-view__hint" data-auto-retry="">
      Lo intentaremos de nuevo automáticamente cada 30 segundos mientras tengas esta página abierta.
    </p>
  );
}

export function UnavailableView({ what, reason, onRetry, level = 1 }: UnavailableProps) {
  const heading = useFocusOnMount<HTMLHeadingElement>();
  const Heading = level === 1 ? "h1" : "h2";
  return (
    <div className="status-view" data-state="unavailable" data-reason={reason}>
      <p className="eyebrow">Servicio no disponible</p>
      <Heading className="heading-2" ref={heading} tabIndex={-1}>
        No pudimos cargar {what}.
      </Heading>
      <p className="lead muted">{REASON_COPY[reason]} Inténtalo de nuevo en unos momentos.</p>
      {reason !== "unconfigured" && (
        <button type="button" className="button" onClick={onRetry}>
          Reintentar
        </button>
      )}
      {retryable(reason) && <AutoRetry onRetry={onRetry} />}
    </div>
  );
}

// Moves focus to a state heading when it replaces the loading state, so
// screen-reader and keyboard users land on the new content.
function useFocusOnMount<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    ref.current?.focus({ preventScroll: true });
  }, []);
  return ref;
}
