// An API image (API_CONTRACT.md §6) with a neutral fallback. Width/height
// come from `format` when known (no layout shift); the frame's aspect ratio is
// set by the caller's CSS, so a missing size never collapses the layout.
import { useState } from "react";
import { resolveMediaUrl, currentApiConfig } from "@/lib/api-config";
import type { MediaAsset } from "@/lib/types";
import { MEDIA_FALLBACK } from "@/content/site";

interface Props {
  media: MediaAsset | null;
  /** Used only when the asset has no alt_text (should not happen for images). */
  fallbackAlt: string;
  loading?: "lazy" | "eager";
  sizes?: string;
  className?: string;
  /** Inside a link already named by the title: alt="" (no duplicate name). */
  decorative?: boolean;
}

export function MediaImage({
  media,
  fallbackAlt,
  loading = "lazy",
  sizes,
  className,
  decorative = false,
}: Props) {
  const src = media ? resolveMediaUrl(media.url, currentApiConfig()) : null;
  const [failed, setFailed] = useState(false);

  if (!src || failed) {
    // Neutral paper surface; announced as "no photograph available".
    return (
      <picture className={className} data-media-fallback="">
        <source type="image/avif" srcSet={MEDIA_FALLBACK.avif} />
        <source type="image/webp" srcSet={MEDIA_FALLBACK.webp} />
        <img
          src={MEDIA_FALLBACK.jpg}
          width={MEDIA_FALLBACK.width}
          height={MEDIA_FALLBACK.height}
          alt={decorative ? "" : `${fallbackAlt}: fotografía no disponible`}
          loading={loading}
          decoding="async"
        />
      </picture>
    );
  }

  return (
    <img
      className={className}
      src={src}
      alt={decorative ? "" : (media?.alt_text ?? fallbackAlt)}
      width={media?.format?.width ?? undefined}
      height={media?.format?.height ?? undefined}
      sizes={sizes}
      loading={loading}
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}
