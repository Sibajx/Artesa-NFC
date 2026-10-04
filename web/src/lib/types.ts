// ArtesaNFC — public API types. Mirror of docs/API_CONTRACT.md §4–§8 and
// backend/app/schemas/*. snake_case on purpose: the contract has no
// camelCase translation layer (§2). Every documented key is always present,
// with null / [] when there is no value (§12).

export type MediaType = "image" | "video" | "model_3d" | "sequence_360";

export type MediaRole =
  "hero" | "gallery" | "detail" | "process" | "portrait" | "document" | "model_3d" | "sequence_360";

export type AvailabilityStatus = "available" | "reserved" | "exhibited" | "archived";

export interface MediaFormat {
  readonly width?: number | null;
  readonly height?: number | null;
  readonly mime_type?: string | null;
  readonly duration_seconds?: number | null;
  readonly format?: string | null;
  readonly file_size_bytes?: number | null;
  readonly frame_count?: number | null;
}

export interface MediaAsset {
  readonly type: MediaType | (string & {});
  readonly role: MediaRole | (string & {});
  readonly url: string;
  readonly alt_text: string | null;
  readonly position: number;
  readonly format: MediaFormat | null;
}

export interface Location {
  readonly locality: string | null;
  readonly municipality: string | null;
  readonly state: string | null;
  readonly country: string | null;
}

export interface ArtisanSummary {
  readonly slug: string;
  readonly full_name: string;
  readonly artistic_name: string | null;
}

export interface PieceSummary {
  readonly slug: string;
  readonly name: string;
  readonly public_code: string;
  readonly availability_status: AvailabilityStatus | (string & {});
  readonly cover_media: MediaAsset | null;
}

export interface Dimensions {
  readonly height: number | null;
  readonly width: number | null;
  readonly depth: number | null;
  readonly unit: string | null;
}

export interface Piece {
  readonly slug: string;
  readonly public_code: string;
  readonly name: string;
  readonly description: string | null;
  readonly history: string | null;
  readonly materials: readonly string[];
  readonly technique: string | null;
  readonly origin: string | null;
  readonly creation_year: number | null;
  readonly creation_date: string | null;
  readonly dimensions: Dimensions | null;
  readonly visual_theme: Readonly<Record<string, unknown>> | null;
  readonly availability_status: AvailabilityStatus | (string & {});
  readonly artisan: ArtisanSummary;
  readonly media: readonly MediaAsset[];
}

export interface Artisan {
  readonly slug: string;
  readonly full_name: string;
  readonly artistic_name: string | null;
  readonly biography: string | null;
  readonly history: string | null;
  readonly location: Location;
  readonly techniques: readonly string[];
  readonly languages: readonly string[];
  // Free JSON without a key contract (API_CONTRACT.md §4): not rendered.
  readonly public_contact: Readonly<Record<string, unknown>> | null;
  readonly media: readonly MediaAsset[];
  readonly pieces: readonly PieceSummary[];
}

export interface ListEnvelope<T> {
  readonly data: readonly T[];
  readonly meta: { readonly total?: number };
}

// POST /certificates/resolve (§7). A single public "unavailable" covers
// unknown, revoked, malformed and any other unusable token: the contract
// forbids telling them apart (§7, §13, SECURITY.md §4).
export interface CertificateAuthentic {
  readonly authenticity: {
    readonly status: "authentic";
    readonly certificate_version: number;
    readonly issued_at: string;
    // ADR-030 phase 3: a custodian reported the piece stolen. Optional so an
    // older API keeps matching.
    readonly reported_stolen?: boolean;
  };
  readonly piece: Piece;
  readonly artisan: Artisan;
  readonly authenticity_metadata: { readonly notes: string | null };
}

export interface CertificateUnavailable {
  readonly authenticity: { readonly status: "unavailable" };
}

export type CertificateResolution = CertificateAuthentic | CertificateUnavailable;

// ADR-030 phase 3: POST /certificates/unlock and /claim. The original
// certificate behind the buyer's card (+ PIN once the piece is claimed).
export interface CertificateOriginal extends CertificateAuthentic {
  readonly result: "unlocked";
  readonly ownership: {
    readonly claimed: boolean;
    readonly claimed_at: string | null;
    readonly owner_email_masked: string | null;
    readonly card_issued_at: string;
  };
  // ADR-030 phase 5: the published design, drawn by the API as SVG.
  readonly design?: {
    readonly version: number;
    readonly svg: string;
    readonly approved_by_name: string | null;
    readonly approved_at: string | null;
  } | null;
}

// P-026 G3: the artisan's authorization link (/autorizacion/#token): what
// will be published about them.
export interface ArtisanAuthorizationOpen {
  readonly status: "open";
  readonly full_name: string;
  readonly artistic_name: string | null;
  readonly place: string;
  readonly biography: string;
  readonly portrait: string | null;
  readonly expires_at: string;
}

// ADR-030 phase 5: the artisan's review link (/revision/#token).
export interface DesignReviewOpen {
  readonly status: "open";
  readonly piece_name: string;
  readonly artisan_name: string;
  readonly version: number;
  readonly expires_at: string;
  readonly svg: string;
}
