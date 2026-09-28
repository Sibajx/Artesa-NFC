// ArtesaNFC — presentation helpers. They only format what the API returned;
// a missing value yields null so the caller omits the row (never invents it).

import type { ArtisanSummary, Dimensions, Location, Piece } from "./types";

export function joinPresent(
  parts: readonly (string | number | null | undefined)[],
  separator: string,
): string {
  return parts
    .filter((part) => part !== null && part !== undefined && String(part).trim() !== "")
    .join(separator);
}

export const displayName = (artisan: ArtisanSummary): string =>
  artisan.artistic_name?.trim() ? artisan.artistic_name : artisan.full_name;

// Community (locality), municipality when it differs, and region (state).
export function formatPlace(location: Location | null | undefined): string | null {
  if (!location) return null;
  const parts: (string | null)[] = [location.locality];
  if (location.municipality && location.municipality !== location.locality) {
    parts.push(location.municipality);
  }
  parts.push(location.state);
  return joinPresent(parts, ", ") || null;
}

const numberFormat = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 1 });

export function formatDimensions(dimensions: Dimensions | null): string | null {
  if (!dimensions) return null;
  const labelled: [string, number | null][] = [
    ["alto", dimensions.height],
    ["ancho", dimensions.width],
    ["fondo", dimensions.depth],
  ];
  const present = labelled.filter((entry): entry is [string, number] => entry[1] !== null);
  if (present.length === 0) return null;
  const unit = dimensions.unit ? ` ${dimensions.unit}` : "";
  return present
    .map(([label, value]) => `${numberFormat.format(value)}${unit} ${label}`)
    .join(" × ");
}

export function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  // Date-only strings (YYYY-MM-DD) are calendar dates: format them in UTC so
  // the visitor's timezone never shifts the day.
  const parsed = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleDateString("es-MX", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: /^\d{4}-\d{2}-\d{2}$/.test(iso) ? "UTC" : "America/Mexico_City",
  });
}

export function formatCreation(
  piece: Pick<Piece, "creation_date" | "creation_year">,
): string | null {
  return (
    formatDate(piece.creation_date) ?? (piece.creation_year ? String(piece.creation_year) : null)
  );
}

const AVAILABILITY_LABELS: Readonly<Record<string, string>> = {
  available: "Disponible",
  reserved: "Reservada",
  exhibited: "En exhibición",
  archived: "Pieza de archivo",
};

// Unknown future enum values are ignored safely (API_CONTRACT.md §13).
export const availabilityLabel = (status: string): string | null =>
  AVAILABILITY_LABELS[status] ?? null;

export function formatBytes(bytes: number | null | undefined): string | null {
  if (!bytes || bytes <= 0) return null;
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${numberFormat.format(bytes / (1024 * 1024))} MB`;
}
