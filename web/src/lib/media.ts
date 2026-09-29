// ArtesaNFC — selecting media from an API `media[]` array (API_CONTRACT.md §6).

import type { MediaAsset } from "./types";

const byPosition = (a: MediaAsset, b: MediaAsset) => a.position - b.position;

export const sortedMedia = (media: readonly MediaAsset[]): MediaAsset[] =>
  [...media].sort(byPosition);

const isImage = (m: MediaAsset) => m.type === "image" && !!m.url;

// Hero photograph of a piece: role=hero first, then any image.
export function pickHeroImage(media: readonly MediaAsset[]): MediaAsset | null {
  const images = sortedMedia(media).filter(isImage);
  return images.find((m) => m.role === "hero") ?? images[0] ?? null;
}

export function pickPortrait(media: readonly MediaAsset[]): MediaAsset | null {
  const images = sortedMedia(media).filter(isImage);
  return images.find((m) => m.role === "portrait") ?? images[0] ?? null;
}

// First GLB model, if any. Only `type` decides: role is informative.
export function pickModel(media: readonly MediaAsset[]): MediaAsset | null {
  return sortedMedia(media).find((m) => m.type === "model_3d" && !!m.url) ?? null;
}

export function imagesWithRoles(
  media: readonly MediaAsset[],
  roles: readonly string[],
  exclude: MediaAsset | null = null,
): MediaAsset[] {
  return sortedMedia(media).filter((m) => isImage(m) && roles.includes(m.role) && m !== exclude);
}
