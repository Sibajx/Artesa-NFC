// ArtesaNFC — the web app's single contractual API layer (docs/API_CONTRACT.md).
//
// Every request goes through here: no component builds an API URL or calls
// fetch() on its own. Requests are time-boxed and never throw; they resolve
// to a tagged result so a page can tell an authoritative answer apart from
// "could not find out":
//
//   { kind: "ok", data }          2xx with the documented shape
//   { kind: "not_found" }         HTTP 404 only (§10: unknown slug, draft,
//                                 archived and a piece under an unpublished
//                                 artisan are deliberately the same 404)
//   { kind: "unavailable", reason } anything else — the page shows a neutral,
//                                 retryable "not available right now" state
//
// The public API is the only authority on publication (F-08): pages must
// never show entity content unless the result is "ok".

import { currentApiConfig, type ApiConfig } from "./api-config";
import type {
  Artisan,
  ArtisanSummary,
  CertificateAuthentic,
  ListEnvelope,
  Piece,
  PieceSummary,
} from "./types";

export const DEFAULT_TIMEOUT_MS = 6000;

export type UnavailableReason =
  | "unconfigured" // host not on the api-config allowlist: no request made
  | "timeout"
  | "network"
  | "rate_limited" // 429
  | "server_error" // 5xx
  | "http_error" // any other non-2xx, non-404
  | "malformed"; // 2xx whose body does not match the contract

export type ApiResult<T> =
  | { readonly kind: "ok"; readonly data: T }
  | { readonly kind: "not_found" }
  | { readonly kind: "unavailable"; readonly reason: UnavailableReason };

// /c/{token}: "unavailable" is a *successful* resolution (§7) and must never
// be merged with a transport or server failure ("error").
export type CertificateResult =
  | { readonly kind: "authentic"; readonly data: CertificateAuthentic }
  | { readonly kind: "unavailable" }
  | { readonly kind: "error"; readonly reason: UnavailableReason };

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface ApiClientOptions {
  readonly config?: ApiConfig;
  readonly fetch?: FetchLike;
  readonly timeoutMs?: number;
}

type Validator<T> = (payload: unknown) => payload is T;

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

const isString = (value: unknown): value is string => typeof value === "string";

function isListOf<T>(item: Validator<T>): Validator<ListEnvelope<T>> {
  return (payload: unknown): payload is ListEnvelope<T> =>
    isRecord(payload) && Array.isArray(payload.data) && payload.data.every(item);
}

// Shape checks are deliberately loose: enough to reject a response that is
// not this contract, without breaking on additive, non-disruptive fields (§13).
export const isPieceSummary: Validator<PieceSummary> = (p): p is PieceSummary =>
  isRecord(p) && isString(p.slug) && isString(p.name) && isString(p.public_code);

export const isArtisanSummary: Validator<ArtisanSummary> = (a): a is ArtisanSummary =>
  isRecord(a) && isString(a.slug) && isString(a.full_name);

export const isPiece: Validator<Piece> = (p): p is Piece =>
  isRecord(p) &&
  isString(p.slug) &&
  isString(p.name) &&
  isString(p.public_code) &&
  isArtisanSummary(p.artisan) &&
  Array.isArray(p.media) &&
  Array.isArray(p.materials);

export const isArtisan: Validator<Artisan> = (a): a is Artisan =>
  isRecord(a) &&
  isString(a.slug) &&
  isString(a.full_name) &&
  isRecord(a.location) &&
  Array.isArray(a.pieces) &&
  a.pieces.every(isPieceSummary) &&
  Array.isArray(a.media) &&
  Array.isArray(a.techniques);

function isCertificateAuthentic(payload: unknown): payload is CertificateAuthentic {
  if (!isRecord(payload) || !isRecord(payload.authenticity)) return false;
  const auth = payload.authenticity;
  return (
    auth.status === "authentic" &&
    typeof auth.certificate_version === "number" &&
    isString(auth.issued_at) &&
    isPiece(payload.piece) &&
    isArtisan(payload.artisan) &&
    isRecord(payload.authenticity_metadata)
  );
}

function isCertificateUnavailable(payload: unknown): boolean {
  return (
    isRecord(payload) &&
    isRecord(payload.authenticity) &&
    payload.authenticity.status === "unavailable"
  );
}

function reasonForStatus(status: number): UnavailableReason {
  if (status === 429) return "rate_limited";
  if (status >= 500) return "server_error";
  return "http_error";
}

export function createApiClient(options: ApiClientOptions = {}) {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const doFetch: FetchLike = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const getConfig = (): ApiConfig => options.config ?? currentApiConfig();

  // Runs one time-boxed request; returns the Response or a failure reason.
  async function send(path: string, init: RequestInit): Promise<Response | UnavailableReason> {
    const base = getConfig().apiBase;
    if (!base) return "unconfigured";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await doFetch(base + path, { ...init, signal: controller.signal });
    } catch {
      return controller.signal.aborted ? "timeout" : "network";
    } finally {
      clearTimeout(timer);
    }
  }

  async function readJson(response: Response): Promise<unknown> {
    try {
      return await response.json();
    } catch {
      return undefined;
    }
  }

  async function getJson<T>(path: string, validate: Validator<T>): Promise<ApiResult<T>> {
    const response = await send(path, { method: "GET", headers: { Accept: "application/json" } });
    if (typeof response === "string") {
      return { kind: "unavailable", reason: response };
    }
    if (response.status === 404) return { kind: "not_found" };
    if (!response.ok) {
      console.warn(`[ArtesaNFC] API request failed: HTTP ${response.status}`);
      return { kind: "unavailable", reason: reasonForStatus(response.status) };
    }
    const payload = await readJson(response);
    if (!validate(payload)) {
      console.warn("[ArtesaNFC] API response does not match the contract.");
      return { kind: "unavailable", reason: "malformed" };
    }
    return { kind: "ok", data: payload };
  }

  const segment = (slug: string) => encodeURIComponent(slug);

  return {
    getPieces: () => getJson("/pieces", isListOf(isPieceSummary)),
    getPiece: (slug: string) => getJson(`/pieces/${segment(slug)}`, isPiece),
    getArtisans: () => getJson("/artisans", isListOf(isArtisanSummary)),
    getArtisan: (slug: string) => getJson(`/artisans/${segment(slug)}`, isArtisan),

    // POST /certificates/resolve (§7). Never logs the token, the URL or the
    // payload; the token only lives in this call's request body.
    async resolveCertificate(token: string): Promise<CertificateResult> {
      const response = await send("/certificates/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ token }),
        credentials: "omit",
        referrerPolicy: "no-referrer",
        cache: "no-store",
      });
      if (typeof response === "string") return { kind: "error", reason: response };
      if (!response.ok) return { kind: "error", reason: reasonForStatus(response.status) };
      const payload = await readJson(response);
      if (isCertificateAuthentic(payload)) return { kind: "authentic", data: payload };
      if (isCertificateUnavailable(payload)) return { kind: "unavailable" };
      return { kind: "error", reason: "malformed" };
    },
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;

let defaultClient: ApiClient | null = null;

export function api(): ApiClient {
  defaultClient ??= createApiClient();
  return defaultClient;
}
