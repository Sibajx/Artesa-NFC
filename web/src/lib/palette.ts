// ADR-030 phase 4: the piece's palette (piece.visual_theme.palette), used to
// dress its public certificate. Defensive: visual_theme is an open JSON
// object, so anything that is not 3-5 "#rrggbb" strings is ignored and the
// page keeps the site's own colours.

const HEX = /^#[0-9a-f]{6}$/i;

export function paletteOf(
  visualTheme: Readonly<Record<string, unknown>> | null | undefined,
): string[] {
  const value = visualTheme?.palette;
  if (!Array.isArray(value)) return [];
  const colors = value
    .filter((c): c is string => typeof c === "string" && HEX.test(c))
    .map((c) => c.toLowerCase());
  return colors.length >= 3 && colors.length <= 5 ? colors : [];
}

function rgb(hex: string): [number, number, number] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
}

function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  ) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function saturation(hex: string): number {
  const [r, g, b] = rgb(hex);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  return max === 0 ? 0 : (max - min) / max;
}

export interface PaletteRoles {
  /** The most saturated colour: rules, seal ring, highlights. */
  readonly accent: string;
  /** The darkest colour. */
  readonly dark: string;
  /** The lightest colour, for soft tints. */
  readonly light: string;
}

export function paletteRoles(colors: readonly string[]): PaletteRoles | null {
  if (colors.length < 3) return null;
  const byLuminance = [...colors].sort((a, b) => luminance(a) - luminance(b));
  const bySaturation = [...colors].sort((a, b) => saturation(b) - saturation(a));
  const accent = bySaturation[0];
  const dark = byLuminance[0];
  const light = byLuminance[byLuminance.length - 1];
  if (!accent || !dark || !light) return null;
  return { accent, dark, light };
}
