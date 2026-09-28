// ArtesaNFC — URL parsing for the neutral detail shells and /c/{token}.
// Route-shape sanity checks only: the API alone decides whether a slug is
// published or a token is real.

export const PIECE_PREFIX = "/piezas/";
export const ARTISAN_PREFIX = "/artesanos/";
export const CERTIFICATE_PREFIX = "/c/";

const MAX_SLUG_LENGTH = 200;
// eslint-disable-next-line no-control-regex
const FORBIDDEN_SLUG_CHARS = /[\u0000-\u001f\u007f/\\]/;
const TOKEN_SHAPE = /^[A-Za-z0-9_-]{1,256}$/;

// /piezas/{slug} or /piezas/{slug}/ — exactly one non-empty segment.
export function slugFromPath(pathname: string, prefix: string): string | null {
  if (!pathname.startsWith(prefix)) return null;
  let rest = pathname.slice(prefix.length);
  if (rest.endsWith("/")) rest = rest.slice(0, -1);
  if (!rest || rest.includes("/")) return null;
  let slug: string;
  try {
    slug = decodeURIComponent(rest);
  } catch {
    return null;
  }
  if (!slug || slug.length > MAX_SLUG_LENGTH || FORBIDDEN_SLUG_CHARS.test(slug)) return null;
  return slug;
}

// Canonical /c/{token} only: one non-empty segment, no trailing slash, no
// nested segment, never a query string (callers never read location.search).
// Same rule as frontend/assets/js/hydrate-certificate.js.
export function tokenFromPath(pathname: string): string | null {
  if (!pathname.startsWith(CERTIFICATE_PREFIX)) return null;
  const rest = pathname.slice(CERTIFICATE_PREFIX.length);
  if (!rest || rest.includes("/")) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(rest);
  } catch {
    return null;
  }
  return TOKEN_SHAPE.test(decoded) ? decoded : null;
}

export const piecePath = (slug: string) => `${PIECE_PREFIX}${encodeURIComponent(slug)}/`;
export const artisanPath = (slug: string) => `${ARTISAN_PREFIX}${encodeURIComponent(slug)}/`;
