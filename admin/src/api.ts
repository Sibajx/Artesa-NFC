// Gestión admin API client (docs/API_CONTRACT.md §14.1, ADR-029).
//
// Same origin: the UI and /api/admin/v1 are served under one hostname behind
// Cloudflare Access, which adds the identity header to every request. No
// token, cookie or credential is handled here.

const BASE = '/api/admin/v1';

export type PublicationStatus = 'draft' | 'published' | 'archived';

export interface ListEnvelope<T> {
  data: T[];
  meta: { total: number };
}

export interface ArtisanSummary {
  id: string;
  slug: string;
  full_name: string;
  artistic_name: string | null;
  publication_status: PublicationStatus;
  piece_count: number;
  updated_at: string;
}

export interface PieceSummary {
  id: string;
  slug: string;
  public_code: string;
  name: string;
  artisan_id: string;
  artisan_slug: string;
  publication_status: PublicationStatus;
  availability_status: string;
  updated_at: string;
}

export interface MediaPublic {
  type: string;
  role: string;
  url: string;
  alt_text: string | null;
  position: number;
}

export interface AdminMedia {
  id: string;
  status: string;
  media: MediaPublic;
}

export interface ArtisanDetail {
  id: string;
  slug: string;
  full_name: string;
  artistic_name: string | null;
  locality: string | null;
  municipality: string | null;
  state: string | null;
  country: string | null;
  languages: unknown[] | null;
  languages_public: boolean;
  biography: string | null;
  history: string | null;
  techniques: unknown[] | null;
  public_contact: Record<string, unknown> | null;
  publication_status: PublicationStatus;
  created_at: string;
  updated_at: string;
  media: AdminMedia[];
  pieces: PieceSummary[];
}

export interface Certificate {
  id: string;
  status: 'draft' | 'active' | 'revoked';
  version: number;
  issued_at: string | null;
  revoked_at: string | null;
  revocation_reason: string | null;
  created_at: string;
}

export interface NfcTag {
  id: string;
  status: string;
  chip_model: string;
  programmed_at: string | null;
  locked_at: string | null;
  created_at: string;
}

export interface PieceDetail {
  id: string;
  slug: string;
  public_code: string;
  name: string;
  description: string | null;
  history: string | null;
  technique: string | null;
  materials: unknown[] | null;
  origin: string | null;
  creation_year: number | null;
  creation_date: string | null;
  dimensions: Record<string, unknown> | null;
  availability_status: string;
  publication_status: PublicationStatus;
  publicly_visible: boolean;
  created_at: string;
  updated_at: string;
  artisan: { id: string; slug: string; full_name: string; publication_status: PublicationStatus };
  media: AdminMedia[];
  certificates: Certificate[];
  nfc_tags: NfcTag[];
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  actor_type: string;
  actor_email: string | null;
  entity_type: string;
  entity_id: string;
  action: string;
  result: 'success' | 'failure';
  metadata: Record<string, unknown> | null;
}

// kind drives what the UI shows; the server's message is never rendered raw.
export type ApiErrorKind = 'session' | 'forbidden' | 'not_found' | 'conflict' | 'unavailable' | 'invalid' | 'network';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number;
  // From the error envelope of a write (409/412/422): a stable code, the
  // field it concerns, and per-field validation details.
  readonly code?: string;
  readonly field?: string;
  readonly fields: Record<string, string>;

  constructor(kind: ApiErrorKind, status: number, body?: ErrorBody) {
    super(kind);
    this.kind = kind;
    this.status = status;
    this.code = body?.error?.code;
    this.field = body?.error?.field;
    this.fields = Object.fromEntries((body?.error?.details ?? []).map((d) => [d.field, d.reason]));
  }
}

interface ErrorBody {
  error?: { code?: string; message?: string; field?: string; details?: { field: string; reason: string }[] };
}

function kindFor(status: number): ApiErrorKind {
  if (status === 401) return 'session';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 409 || status === 412 || status === 428) return 'conflict';
  if (status === 422 || status === 400) return 'invalid';
  return 'unavailable';
}

async function request<T>(method: string, path: string, init: { params?: Record<string, string | undefined>; body?: unknown; version?: string; signal?: AbortSignal } = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(init.params ?? {})) {
    if (value) query.set(key, value);
  }
  const url = `${BASE}${path}${query.size ? `?${query}` : ''}`;
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (method !== 'GET') {
    // The API refuses writes without this header and a JSON body (CSRF
    // guard, API_CONTRACT §14.2); If-Match carries the version on screen.
    headers['X-Artesa-Admin'] = '1';
    headers['Content-Type'] = 'application/json';
    if (init.version) headers['If-Match'] = init.version;
  }
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: method === 'GET' ? undefined : JSON.stringify(init.body ?? {}),
      credentials: 'same-origin',
      // An expired Access session answers with a redirect to the login page;
      // following it would hand an HTML page to the JSON parser.
      redirect: 'manual',
      signal: init.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('network', 0);
  }
  if (response.type === 'opaqueredirect' || response.status === 0) {
    throw new ApiError('session', 0);
  }
  if (!response.ok) {
    let body: ErrorBody | undefined;
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      body = undefined;
    }
    throw new ApiError(kindFor(response.status), response.status, body);
  }
  return (await response.json()) as T;
}

function get<T>(path: string, params?: Record<string, string | undefined>, signal?: AbortSignal): Promise<T> {
  return request<T>('GET', path, { params, signal });
}

export type Transition = 'publish' | 'unpublish' | 'archive' | 'restore';

export interface ArtisanInput {
  full_name?: string;
  slug?: string;
  artistic_name?: string | null;
  locality?: string | null;
  municipality?: string | null;
  state?: string | null;
  country?: string | null;
  languages?: string[] | null;
  languages_public?: boolean;
  biography?: string | null;
  history?: string | null;
  techniques?: string[] | null;
  public_contact?: Record<string, string> | null;
}

export interface PieceInput {
  artisan_id?: string;
  name?: string;
  slug?: string;
  public_code?: string;
  description?: string | null;
  history?: string | null;
  technique?: string | null;
  materials?: string[] | null;
  origin?: string | null;
  creation_year?: number | null;
  dimensions?: Record<string, number | string> | null;
  availability_status?: string;
}

export const adminApi = {
  me: (signal?: AbortSignal) => get<{ email: string }>('/me', undefined, signal),
  artisans: (params: { publication_status?: string; q?: string }, signal?: AbortSignal) =>
    get<ListEnvelope<ArtisanSummary>>('/artisans', params, signal),
  artisan: (id: string, signal?: AbortSignal) => get<ArtisanDetail>(`/artisans/${encodeURIComponent(id)}`, undefined, signal),
  pieces: (params: { publication_status?: string; artisan_id?: string; q?: string }, signal?: AbortSignal) =>
    get<ListEnvelope<PieceSummary>>('/pieces', params, signal),
  piece: (id: string, signal?: AbortSignal) => get<PieceDetail>(`/pieces/${encodeURIComponent(id)}`, undefined, signal),
  auditEvents: (params: { entity_type?: string; entity_id?: string; limit?: string }, signal?: AbortSignal) =>
    get<ListEnvelope<AuditEvent>>('/audit-events', params, signal),

  createArtisan: (body: ArtisanInput) => request<ArtisanDetail>('POST', '/artisans', { body }),
  updateArtisan: (id: string, version: string, body: ArtisanInput) =>
    request<ArtisanDetail>('PATCH', `/artisans/${encodeURIComponent(id)}`, { body, version }),
  transitionArtisan: (id: string, version: string, action: Transition, reason?: string) =>
    request<ArtisanDetail>('POST', `/artisans/${encodeURIComponent(id)}/${action}`, { body: reason ? { reason } : {}, version }),

  createPiece: (body: PieceInput) => request<PieceDetail>('POST', '/pieces', { body }),
  updatePiece: (id: string, version: string, body: PieceInput) =>
    request<PieceDetail>('PATCH', `/pieces/${encodeURIComponent(id)}`, { body, version }),
  transitionPiece: (id: string, version: string, action: Transition, reason?: string) =>
    request<PieceDetail>('POST', `/pieces/${encodeURIComponent(id)}/${action}`, { body: reason ? { reason } : {}, version }),
  setAvailability: (id: string, version: string, availability_status: string) =>
    request<PieceDetail>('POST', `/pieces/${encodeURIComponent(id)}/availability`, { body: { availability_status }, version }),
};

// Cloudflare Access ends the session at this path on the protected hostname.
export const LOGOUT_URL = '/cdn-cgi/access/logout';
