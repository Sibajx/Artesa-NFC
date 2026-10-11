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
  trashed_at?: string | null;
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
  trashed_at?: string | null;
}

export type MediaRole = 'hero' | 'gallery' | 'detail' | 'process' | 'portrait' | 'model_3d';

export interface MediaPublic {
  type: 'image' | 'video' | 'model_3d' | string;
  role: MediaRole | string;
  url: string;
  alt_text: string | null;
  position: number;
  format: Record<string, unknown>;
}

export interface AdminMedia {
  id: string;
  status: 'active' | 'archived';
  updated_at: string;
  media: MediaPublic;
  // True only for media that were never public: they can be deleted for good.
  deletable: boolean;
}

// Published files live under /media/ on the public API host (docs/MEDIA.md
// §4). On gestion.artesanfc.com that path belongs to the UI's static server,
// so previews are loaded from the API host; `npm run dev` proxies /media/.
const MEDIA_ORIGIN: string = import.meta.env.VITE_MEDIA_ORIGIN ?? (import.meta.env.DEV ? '' : 'https://api.artesanfc.com');

export function mediaUrl(url: string): string {
  return `${MEDIA_ORIGIN}${url}`;
}

export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

export interface ArtisanAuthorization {
  id: string;
  // 'changes_requested' and 'declined' only come back as `last_answer`.
  status: 'pending' | 'authorized' | 'changes_requested' | 'declined';
  medium: string;
  requested_by: string;
  created_at: string;
  expires_at: string | null;
  decided_at: string | null;
  note: string | null;
}

// P-026 G4: the owner's accounts page.
export interface Account {
  email: string;
  role: 'editor' | 'designer' | 'custodian' | 'hero' | 'designer_hero';
  source: 'configuracion' | 'gestion';
  owner: boolean;
  added_by: string | null;
  added_at: string | null;
  note: string | null;
  // The checkboxes the account holds; `custom` = ticked by hand (not just what its role gives).
  permissions: string[];
  custom: boolean;
  // A fixed account (server config) that already has a row here, so it can be edited.
  imported: boolean;
}

export interface AccountList {
  data: Account[];
  // The checkboxes in order, and what each role (a shortcut) gives.
  catalog: string[];
  presets: Record<string, string[]>;
  cloudflare: string;
  sync_configured: boolean;
}

// Insumos (stock of chips, seals, scratch cards, epoxy...) and the inventory of pieces.
export interface Supply {
  id: string;
  name: string;
  unit: string;
  min_stock: string;
  stock: string;
  low: boolean;
  note: string | null;
  active: boolean;
}

export interface SupplyList {
  data: Supply[];
  low_count: number;
}

export interface SupplyMovement {
  id: string;
  kind: 'purchase' | 'use' | 'loss' | 'adjustment';
  delta: string;
  unit_cost_cents: number | null;
  piece_id: string | null;
  piece_name: string | null;
  note: string | null;
  recorded_by: string;
  created_at: string;
}

export interface SupplyDetail {
  supply: Supply;
  movements: SupplyMovement[];
}

export interface SupplyMovementInput {
  kind: SupplyMovement['kind'];
  quantity: number;
  direction?: 'up' | 'down';
  unit_cost_cents?: number | null;
  piece_id?: string | null;
  note?: string | null;
}

export interface InventoryRow {
  id: string;
  public_code: string;
  name: string;
  artisan_id: string;
  artisan_name: string;
  publication_status: PublicationStatus;
  availability_status: string;
  price_cents: number | null;
  price_currency: string;
  location: string | null;
  place: string | null;
  sold_on: string | null;
  updated_at: string;
}

export interface Inventory {
  summary: { total: number; by_availability: Record<string, number>; available_value_cents: number; currency: string };
  data: InventoryRow[];
}

// P-028: the home hero by season.
export interface HeroCampaign {
  id: string;
  slug: string;
  name: string;
  is_default: boolean;
  start_month: number | null;
  start_day: number | null;
  end_month: number | null;
  end_day: number | null;
  status: 'no_video' | 'processing' | 'error' | 'draft' | 'live' | 'scheduled';
  error: string | null;
  published: boolean;
  forced: boolean;
  forced_until: string | null;
  video_mp4: string | null;
  video_webm: string | null;
  poster: string | null;
  updated_by: string | null;
  updated_at: string;
}

export interface HeroState {
  today: string;
  ffmpeg_available: boolean;
  media_enabled: boolean;
  live_id: string | null;
  live_reason: 'forced' | 'date' | 'default' | null;
  campaigns: HeroCampaign[];
}

// P-029: the replaceable images of the public site.
export interface SiteImage {
  avif: string;
  webp: string;
  jpg: string;
  width: number;
  height: number;
  updated_by: string | null;
  updated_at: string;
}

export interface SiteSlot {
  slot: string;
  label: string;
  ratio: string;
  image: SiteImage | null;
}

export interface SiteImagesState {
  media_enabled: boolean;
  slots: SiteSlot[];
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
  // P-026 G3: private WhatsApp for validations and the authorization to publish.
  validation_whatsapp?: string | null;
  validation_contact_name?: string | null;
  authorization?: ArtisanAuthorization | null;
  // The artisan's latest "Quiero cambios" / "No autorizo" (comment in `note`),
  // until a new link replaces it.
  last_answer?: ArtisanAuthorization | null;
  publication_status: PublicationStatus;
  created_at: string;
  updated_at: string;
  media: AdminMedia[];
  pieces: PieceSummary[];
  // Papelera: set while in the trash; purge_blocker is null when it can be
  // deleted for good, else the reason code.
  trashed_at?: string | null;
  purge_blocker?: string | null;
}

// ADR-030 custody overview (custodians only).
export interface CustodyPiece {
  id: string;
  slug: string;
  public_code: string;
  name: string;
  artisan_name: string;
  publication_status: PublicationStatus;
  certificate_status: 'draft' | 'active' | 'revoked' | null;
  certificate_version: number | null;
  tag_status: 'available' | 'programmed' | 'locked' | null;
  tag_chip: string | null;
  ready_to_certify: boolean;
  // P-026 G6.
  card_status?: 'active' | 'blocked' | null;
  claimed?: boolean;
  design_status?: string | null;
  sold?: boolean;
  reported_stolen?: boolean;
}

export interface CustodyTag {
  id: string;
  status: 'available' | 'programmed' | 'locked';
  uid: string | null;
  programmed_at: string | null;
  locked_at: string | null;
}

export interface CustodyState {
  piece_id: string;
  public_code: string;
  name: string;
  artisan_name: string;
  piece_published: boolean;
  artisan_published: boolean;
  certificate_active: boolean;
  certificate_issued_at: string | null;
  revoked_certificates: number;
  tags: CustodyTag[];
  recommended_action: 'issue' | 'verify_then_optional_lock' | 'locked' | 'interrupted_rotate' | 'revoked_with_tags';
  issue_blockers: string[];
  rotate_blockers: string[];
  lock_blockers: string[];
  revocation_reasons: string[];
  // Out-of-service chips that were never locked: they can be freed and written again.
  releasable_tags?: { id: string; status: 'replaced' | 'retired'; uid: string }[];
  // ADR-030 phase 3.
  card: { status: 'active' | 'blocked'; issued_at: string; failed_attempts: number; locked_until: string | null } | null;
  claim: { owner_email: string; claimed_at: string; verified_until: string | null } | null;
  reported_stolen_at: string | null;
}

// The only API response that carries the buyer's card key (ADR-030). Shown
// once for printing; never stored or logged.
export interface CardKey {
  key: string;
  public_code: string;
}

// The only API response that carries the certificate URL (ADR-030). Keep it
// in memory for the write; never render or log it.
export interface CustodyIssued {
  url: string;
  certificate_id: string;
  tag_id: string;
  uid: string | null;
  needs_program: boolean;
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
  // P-026: list price in cents (Gestión only) and every sale, newest first.
  price_cents?: number | null;
  price_currency?: string;
  sales?: Sale[];
  // P-026 G12: where the piece is, newest move first.
  locations?: PieceLocation[];
  // ADR-030 phase 4: { palette: string[], palette_source: 'auto' | 'manual' } and any other keys.
  visual_theme: Record<string, unknown> | null;
  availability_status: string;
  publication_status: PublicationStatus;
  publicly_visible: boolean;
  created_at: string;
  updated_at: string;
  artisan: { id: string; slug: string; full_name: string; publication_status: PublicationStatus; trashed_at?: string | null };
  media: AdminMedia[];
  certificates: Certificate[];
  nfc_tags: NfcTag[];
  // False for non-custodians: certificates and nfc_tags are hidden (ADR-030).
  custody_visible?: boolean;
  trashed_at?: string | null;
  purge_blocker?: string | null;
}

// ADR-030 phase 5: the designed original certificate.
export interface DesignParams {
  template: 'clasico' | 'greca' | 'constelacion';
  variant: 'claro' | 'oscuro';
  title: string;
  piece_name: string;
  artisan_name: string;
  public_code: string;
  quote: string;
  palette: string[];
  seed: number;
  // ADR-030 phase 5b: the team's artwork (null removes it).
  art?: DesignArt | null;
  // "Pieza X de N" in the footer; absent or null prints "Pieza única".
  edition?: { number: number; total: number } | null;
}

export type ArtPlacement = 'sello' | 'encabezado' | 'fondo';

export interface DesignArt {
  id: string;
  placement: ArtPlacement;
  opacity: number;
}

export interface Design {
  id: string;
  piece_id: string;
  version: number;
  status: 'draft' | 'in_review' | 'approved' | 'published' | 'superseded';
  template: string;
  params: DesignParams;
  created_by: string;
  created_at: string;
  updated_at: string;
  submitted_at: string | null;
  review_expires_at: string | null;
  change_request: string | null;
  approved_at: string | null;
  approved_by_name: string | null;
  approval_medium: string | null;
  approval_note: string | null;
  approval_recorded_by: string | null;
  published_at: string | null;
  published_by: string | null;
  svg?: string;
}

// P-026 G1: the sale of a piece.
export interface Sale {
  id: string;
  status: 'active' | 'cancelled';
  sold_on: string;
  price_cents: number;
  currency: string;
  channel: 'taller' | 'tienda' | 'en_linea' | 'feria' | 'otro';
  sold_by: string;
  buyer_name: string | null;
  buyer_contact: string | null;
  note: string | null;
  recorded_by: string;
  created_at: string;
  cancelled_at: string | null;
  cancel_reason: string | null;
  cancelled_by: string | null;
}

export interface SaleInput {
  sold_on: string;
  price_cents: number;
  currency: string;
  channel: Sale['channel'];
  sold_by: string;
  buyer_name: string | null;
  buyer_contact: string | null;
  note: string | null;
}

// P-026 G12: one move of a piece; the newest is where it is now.
export type LocationKind = 'taller' | 'bodega' | 'tienda' | 'exhibicion' | 'transito' | 'entregada' | 'otro';

export interface PieceLocation {
  id: string;
  location: LocationKind;
  place: string | null;
  moved_on: string;
  note: string | null;
  recorded_by: string;
  created_at: string;
}

export interface LocationInput {
  location: LocationKind;
  place: string | null;
  moved_on: string;
  note: string | null;
}

// P-026 G5/G8: what needs attention, shaped by role.
export interface SummaryBucket {
  count: number;
  items: { id: string; name: string; detail: string | null }[];
}

export interface Summary {
  published_artisans_without_authorization: SummaryBucket;
  authorizations_waiting: SummaryBucket;
  authorizations_with_changes_requested?: SummaryBucket;
  sales_last_30_days: { count: number; total_cents: number };
  recent_answers: { at: string; text: string; link: string; positive: boolean }[];
  designs_in_review?: SummaryBucket;
  designs_to_publish?: SummaryBucket;
  designs_with_changes_requested?: SummaryBucket;
  published_without_certificate?: SummaryBucket;
  certified_without_chip?: SummaryBucket;
  certified_without_card?: SummaryBucket;
  reported_stolen?: SummaryBucket;
  cards_blocked_or_locked?: SummaryBucket;
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
  if (status === 422 || status === 400 || status === 413 || status === 415) return 'invalid';
  return 'unavailable';
}

interface RequestInit_ {
  params?: Record<string, string | undefined>;
  body?: unknown;
  // Upload: the file itself is the body, sent with its own content type.
  file?: { data: Blob; contentType: string };
  version?: string;
  signal?: AbortSignal;
}

async function request<T>(method: string, path: string, init: RequestInit_ = {}): Promise<T> {
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
    headers['Content-Type'] = init.file ? init.file.contentType : 'application/json';
    if (init.version) headers['If-Match'] = init.version;
  }
  const body = method === 'GET' ? undefined : init.file ? init.file.data : JSON.stringify(init.body ?? {});
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body,
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
  if (response.status === 204) return undefined as T;
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
  validation_whatsapp?: string | null;
  validation_contact_name?: string | null;
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
  price_cents?: number | null;
  price_currency?: string;
}

export const adminApi = {
  me: (signal?: AbortSignal) => get<{ email: string; roles: string[]; permissions: string[] }>('/me', undefined, signal),
  custodyPieces: (signal?: AbortSignal) => get<ListEnvelope<CustodyPiece>>('/custody/pieces', undefined, signal),
  custodyState: (id: string, signal?: AbortSignal) => get<CustodyState>(`/custody/pieces/${encodeURIComponent(id)}/state`, undefined, signal),
  custodyIssue: (id: string, uid: string) => request<CustodyIssued>('POST', `/custody/pieces/${encodeURIComponent(id)}/issue`, { body: { uid } }),
  custodyRotate: (id: string, reason: string, uid: string | null) =>
    request<CustodyIssued>('POST', `/custody/pieces/${encodeURIComponent(id)}/rotate`, { body: { reason, uid } }),
  custodyProgram: (id: string, tagId: string, uid: string) =>
    request<CustodyState>('POST', `/custody/pieces/${encodeURIComponent(id)}/program`, { body: { tag_id: tagId, uid } }),
  custodyLock: (id: string, uid: string) => request<CustodyState>('POST', `/custody/pieces/${encodeURIComponent(id)}/lock`, { body: { uid } }),
  custodyRevoke: (id: string, reason: string) => request<CustodyState>('POST', `/custody/pieces/${encodeURIComponent(id)}/revoke`, { body: { reason } }),
  custodyReleaseTag: (id: string, tagId: string) =>
    request<CustodyState>('POST', `/custody/pieces/${encodeURIComponent(id)}/tags/${encodeURIComponent(tagId)}/release`, { body: {} }),
  cardIssue: (id: string) => request<CardKey>('POST', `/custody/pieces/${encodeURIComponent(id)}/card/issue`, { body: {} }),
  // override: only the project owner, when the buyer lost access to their email.
  cardKeyAction: (id: string, action: 'card/replace' | 'transfer', note: string, override = false) =>
    request<CardKey>('POST', `/custody/pieces/${encodeURIComponent(id)}/${action}`,
      { body: override ? { note, override_owner_check: true } : { note } }),
  ownershipAction: (id: string, action: 'card/block' | 'card/unblock' | 'claim/release' | 'stolen' | 'stolen/clear', note: string, override = false) =>
    request<CustodyState>('POST', `/custody/pieces/${encodeURIComponent(id)}/${action}`,
      { body: override ? { note, override_owner_check: true } : { note } }),
  // The owner's email code: sent to the claim's address, read out by the owner,
  // confirmed here. It lets one card replacement or claim release go ahead.
  ownerCodeSend: (id: string) =>
    request<{ sent_to: string }>('POST', `/custody/pieces/${encodeURIComponent(id)}/owner-code/send`, { body: {} }),
  ownerCodeVerify: (id: string, code: string) =>
    request<CustodyState>('POST', `/custody/pieces/${encodeURIComponent(id)}/owner-code/verify`, { body: { code } }),
  artisans: (params: { publication_status?: string; q?: string; trashed?: string }, signal?: AbortSignal) =>
    get<ListEnvelope<ArtisanSummary>>('/artisans', params, signal),
  artisan: (id: string, signal?: AbortSignal) => get<ArtisanDetail>(`/artisans/${encodeURIComponent(id)}`, undefined, signal),
  pieces: (params: { publication_status?: string; artisan_id?: string; q?: string; trashed?: string }, signal?: AbortSignal) =>
    get<ListEnvelope<PieceSummary>>('/pieces', params, signal),
  piece: (id: string, signal?: AbortSignal) => get<PieceDetail>(`/pieces/${encodeURIComponent(id)}`, undefined, signal),
  summary: (signal?: AbortSignal) => get<Summary>('/summary', undefined, signal),
  auditEvents: (params: { entity_type?: string; entity_id?: string; limit?: string; action_prefix?: string;
    actor_email?: string; since?: string; until?: string }, signal?: AbortSignal) =>
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
  // Papelera: trash / untrash return the record; purge deletes it (204).
  trashAction: (kind: 'artisans' | 'pieces', id: string, version: string, action: 'trash' | 'untrash') =>
    request<ArtisanDetail | PieceDetail>('POST', `/${kind}/${encodeURIComponent(id)}/${action}`, { body: {}, version }),
  purge: (kind: 'artisans' | 'pieces', id: string, version: string) =>
    request<void>('POST', `/${kind}/${encodeURIComponent(id)}/purge`, { body: {}, version }),
  setAvailability: (id: string, version: string, availability_status: string) =>
    request<PieceDetail>('POST', `/pieces/${encodeURIComponent(id)}/availability`, { body: { availability_status }, version }),
  designs: (pieceId: string, signal?: AbortSignal) =>
    get<{ data: Design[] }>(`/pieces/${encodeURIComponent(pieceId)}/designs`, undefined, signal),
  design: (id: string, signal?: AbortSignal) => get<Design>(`/designs/${encodeURIComponent(id)}`, undefined, signal),
  createDesign: (pieceId: string) => request<Design>('POST', `/pieces/${encodeURIComponent(pieceId)}/designs`, { body: {} }),
  uploadArt: (file: Blob, contentType: string) =>
    request<{ id: string; mime_type: string; width: number; height: number }>('POST', '/certificate-art', {
      file: { data: file, contentType },
    }),
  previewDesign: (params: DesignParams, version: number) =>
    request<{ svg: string }>('POST', '/designs/preview', { body: { params, version } }),
  updateDesign: (id: string, params: Partial<DesignParams>, version: string) =>
    request<Design>('PATCH', `/designs/${encodeURIComponent(id)}`, { body: { params }, version }),
  submitDesign: (id: string, version: string) =>
    request<Design & { review_url: string }>('POST', `/designs/${encodeURIComponent(id)}/submit`, { body: {}, version }),
  approveDesign: (id: string, body: { name: string; medium: string; note: string }, version: string) =>
    request<Design>('POST', `/designs/${encodeURIComponent(id)}/approve`, { body, version }),
  publishDesign: (id: string, version: string) =>
    request<Design>('POST', `/designs/${encodeURIComponent(id)}/publish`, { body: {}, version }),
  discardDesign: (id: string, version: string) =>
    request<void>('POST', `/designs/${encodeURIComponent(id)}/discard`, { body: {}, version }),
  accounts: (signal?: AbortSignal) => get<AccountList>('/accounts', undefined, signal),
  addAccount: (email: string, role: string, note: string | null) =>
    request<AccountList>('POST', '/accounts', { body: { email, role, note } }),
  changeAccountRole: (email: string, role: string) =>
    request<AccountList>('POST', `/accounts/${encodeURIComponent(email)}/role`, { body: { role } }),
  removeAccount: (email: string) => request<AccountList>('POST', `/accounts/${encodeURIComponent(email)}/remove`, { body: {} }),
  syncAccounts: () => request<AccountList>('POST', '/accounts/sync', { body: {} }),
  inventory: (signal?: AbortSignal) => get<Inventory>('/inventory', undefined, signal),
  supplies: (includeInactive: boolean, signal?: AbortSignal) =>
    get<SupplyList>('/supplies', includeInactive ? { include_inactive: 'true' } : undefined, signal),
  supply: (id: string, signal?: AbortSignal) => get<SupplyDetail>(`/supplies/${encodeURIComponent(id)}`, undefined, signal),
  createSupply: (body: { name: string; unit: string; min_stock: number; note: string | null }) =>
    request<SupplyDetail>('POST', '/supplies', { body }),
  updateSupply: (id: string, body: Partial<{ name: string; unit: string; min_stock: number; note: string | null; active: boolean }>) =>
    request<SupplyDetail>('PATCH', `/supplies/${encodeURIComponent(id)}`, { body }),
  recordSupplyMovement: (id: string, body: SupplyMovementInput) =>
    request<SupplyDetail>('POST', `/supplies/${encodeURIComponent(id)}/movements`, { body }),
  setAccountPermissions: (email: string, permissions: string[] | null) =>
    request<AccountList>('POST', `/accounts/${encodeURIComponent(email)}/permissions`, { body: { permissions } }),
  importFixedAccounts: () => request<AccountList>('POST', '/accounts/import-fixed', { body: {} }),
  requestAuthorization: (id: string) =>
    request<{ url: string; whatsapp: string | null; contact_name: string | null; artisan_name: string }>(
      'POST', `/artisans/${encodeURIComponent(id)}/authorization/request`, { body: {} }),
  recordAuthorization: (id: string, note: string) =>
    request<ArtisanDetail>('POST', `/artisans/${encodeURIComponent(id)}/authorization/record`, { body: { note } }),
  revokeAuthorization: (id: string, note: string) =>
    request<ArtisanDetail>('POST', `/artisans/${encodeURIComponent(id)}/authorization/revoke`, { body: { note } }),
  movePiece: (id: string, body: LocationInput, version: string) =>
    request<PieceDetail>('POST', `/pieces/${encodeURIComponent(id)}/location`, { body, version }),
  registerSale: (id: string, body: SaleInput, version: string) =>
    request<PieceDetail>('POST', `/pieces/${encodeURIComponent(id)}/sale`, { body, version }),
  cancelSale: (id: string, reason: string, version: string) =>
    request<PieceDetail>('POST', `/pieces/${encodeURIComponent(id)}/sale/cancel`, { body: { reason }, version }),
  generatePalette: (id: string, version: string) =>
    request<PieceDetail>('POST', `/pieces/${encodeURIComponent(id)}/palette/generate`, { body: {}, version }),
  setPalette: (id: string, colors: string[], version: string) =>
    request<PieceDetail>('POST', `/pieces/${encodeURIComponent(id)}/palette`, { body: { colors }, version }),

  hero: (signal?: AbortSignal) => get<HeroState>('/hero', undefined, signal),
  createHeroCampaign: (body: { name: string; start_month: number; start_day: number; end_month: number; end_day: number }) =>
    request<HeroState>('POST', '/hero/campaigns', { body }),
  updateHeroCampaign: (id: string, body: { name?: string; start_month?: number; start_day?: number; end_month?: number; end_day?: number }) =>
    request<HeroState>('PATCH', `/hero/campaigns/${encodeURIComponent(id)}`, { body }),
  deleteHeroCampaign: (id: string) => request<HeroState>('DELETE', `/hero/campaigns/${encodeURIComponent(id)}`, { body: {} }),
  publishHeroCampaign: (id: string, published: boolean) =>
    request<HeroState>('POST', `/hero/campaigns/${encodeURIComponent(id)}/${published ? 'publish' : 'unpublish'}`, { body: {} }),
  forceHeroCampaign: (id: string, until: string | null) =>
    request<HeroState>('POST', '/hero/force', { body: { campaign_id: id, until } }),
  unforceHero: () => request<HeroState>('DELETE', '/hero/force', { body: {} }),
  uploadHeroVideo: (id: string, file: Blob, contentType: string, start = 0) =>
    request<HeroState>('POST', `/hero/campaigns/${encodeURIComponent(id)}/video`, {
      params: { start: start > 0 ? String(start) : undefined },
      file: { data: file, contentType },
    }),

  siteImages: (signal?: AbortSignal) => get<SiteImagesState>('/hero/site-images', undefined, signal),
  uploadSiteImage: (slot: string, file: Blob, contentType: string) =>
    request<SiteImagesState>('POST', `/hero/site-images/${encodeURIComponent(slot)}`, { file: { data: file, contentType } }),
  clearSiteImage: (slot: string) =>
    request<SiteImagesState>('DELETE', `/hero/site-images/${encodeURIComponent(slot)}`, { body: {} }),

  uploadMedia: (owner: 'artisans' | 'pieces', id: string, file: Blob, contentType: string, role: MediaRole, altText?: string) =>
    request<AdminMedia>('POST', `/${owner}/${encodeURIComponent(id)}/media`, {
      params: { role, alt_text: altText },
      file: { data: file, contentType },
    }),
  updateMedia: (id: string, version: string, body: { alt_text?: string | null; position?: number; role?: MediaRole }) =>
    request<AdminMedia>('PATCH', `/media/${encodeURIComponent(id)}`, { body, version }),
  deleteMedia: (id: string, version: string) =>
    request<void>('DELETE', `/media/${encodeURIComponent(id)}`, { body: {}, version }),
  transitionMedia: (id: string, version: string, action: 'archive' | 'restore') =>
    request<AdminMedia>('POST', `/media/${encodeURIComponent(id)}/${action}`, { body: {}, version }),
};

// Cloudflare Access ends the session at this path on the protected hostname.
// P-026 G11: a plain link downloads it; Cloudflare Access covers the request.
export const PIECES_CSV_URL = `${BASE}/exports/pieces.csv`;

export const LOGOUT_URL = '/cdn-cgi/access/logout';
