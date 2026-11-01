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

// 2026-11 — the piece page hero is a field of the piece's own colour, like
// its chapter on artesanfc.com (galeria/assets/js/render.js chapterColours):
// the most vivid palette colour, toned towards wood, then darkened until the
// cream text on it reads at AA (4.5:1). null when the palette is unusable;
// the page then keeps its default field.

const WOOD = "#5c3f28";
const NIGHT = "#0d0b0a";
const CREAM = "#efe3cc";

function mix(a: string, b: string, amount: number): string {
  const x = rgb(a);
  const y = rgb(b);
  return (
    "#" +
    x
      .map((c, i) => Math.round((c + ((y[i] ?? 0) - c) * amount) * 255))
      .map((v) => v.toString(16).padStart(2, "0"))
      .join("")
  );
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((p, q) => q - p) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

export function pieceField(
  visualTheme: Readonly<Record<string, unknown>> | null | undefined,
): string | null {
  const vivid = paletteOf(visualTheme)
    .map((c) => {
      const [r, g, b] = rgb(c);
      return { c, v: Math.max(r, g, b), s: saturation(c) };
    })
    .filter((x) => x.s > 0.25 && x.v > 0.3)
    .sort((p, q) => q.s * q.v - p.s * p.v)[0];
  if (!vivid) return null;
  let field = mix(vivid.c, WOOD, 0.28);
  for (let i = 0; i < 12 && contrastRatio(field, CREAM) < 4.5; i += 1) {
    field = mix(field, NIGHT, 0.12);
  }
  return field;
}
