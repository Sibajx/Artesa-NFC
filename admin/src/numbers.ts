// Numeric fields of the piece form. People type on phones, so the fields filter what
// can be typed, and whatever is left over is reported on save instead of being dropped
// without a word.

/** Keep only what a price can contain: digits, separators, "$" and spaces. */
export const typedPrice = (raw: string): string => raw.replace(/[^0-9.,$\s]/g, '');

/** Keep only what a measure can contain: digits and one kind of decimal separator. */
export const typedMeasure = (raw: string): string => raw.replace(/[^0-9.,]/g, '');

/** "$1,250.50" or "1250,5" to cents; null when it is not a valid amount. */
export function priceCents(raw: string): number | null {
  const cleaned = raw.replace(/[$\s]/g, '').replace(/,(?=\d{3}(\D|$))/g, '').replace(',', '.');
  if (!cleaned) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) && n >= 0 ? Math.round(n * 100) : null;
}

/** An error text, or undefined when the price is blank (optional) or valid. */
export function priceError(raw: string): string | undefined {
  if (!raw.trim()) return undefined;
  return priceCents(raw) === null ? 'El precio debe ser un número, por ejemplo 3500 o 3,500.00.' : undefined;
}

export function measureError(raw: string): string | undefined {
  if (!raw.trim()) return undefined;
  const n = Number(raw.replace(',', '.'));
  return Number.isFinite(n) && n > 0 ? undefined : 'Escribe un número mayor que 0, por ejemplo 25 o 12.5.';
}

export const MIN_YEAR = 1000;

/** The year of creation cannot be in the future (the server accepts up to 2100). */
export function yearError(raw: string, now: Date = new Date()): string | undefined {
  if (!raw.trim()) return undefined;
  const year = Number(raw);
  if (!Number.isInteger(year) || year < MIN_YEAR) return 'Escribe un año de cuatro cifras, por ejemplo 2024.';
  const max = now.getFullYear();
  return year > max ? `El año no puede ser futuro (máximo ${max}).` : undefined;
}

/** Today as `YYYY-MM-DD` in the browser's own time zone, for the `max` of date fields. */
export function todayLocal(now: Date = new Date()): string {
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

/** `type="number"` still lets "e", "+" and "-" through; this keeps them out of the keyboard. */
export function blockNumberKeys(e: { key: string; preventDefault: () => void }): void {
  if (e.key === 'e' || e.key === 'E' || e.key === '+' || e.key === '-') e.preventDefault();
}

/** A tracking number is letters, digits and dashes; spaces and symbols are dropped as typed. */
export const typedTracking = (raw: string): string => raw.replace(/[^A-Za-z0-9-]/g, '');
