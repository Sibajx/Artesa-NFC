// Card for a piece summary (DESIGN_SYSTEM.md §9: image, name, origin/artisan,
// discreet action). The whole card is one link; extra actions (3D) sit
// outside the link so there is never a control nested in a link.
import type { ReactNode } from "react";
import { availabilityLabel } from "@/lib/format";
import { piecePath } from "@/lib/routes";
import type { PieceSummary } from "@/lib/types";
import { MediaImage } from "./MediaImage";

interface Props {
  piece: PieceSummary;
  /** Secondary line, e.g. the artisan name when known. */
  meta?: string | null;
  badge?: ReactNode;
  actions?: ReactNode;
  headingLevel?: 2 | 3;
}

export function PieceCard({ piece, meta, badge, actions, headingLevel = 2 }: Props) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const availability = availabilityLabel(piece.availability_status);
  return (
    <article className="piece-card">
      <a className="piece-card__link" href={piecePath(piece.slug)}>
        <div className="piece-card__media media-frame">
          <MediaImage
            media={piece.cover_media}
            fallbackAlt={piece.name}
            decorative
            sizes="(min-width: 1100px) 33vw, (min-width: 640px) 50vw, 100vw"
          />
          {badge}
        </div>
        <Heading className="piece-card__title">{piece.name}</Heading>
      </a>
      <p className="piece-card__meta">
        <span className="piece-card__code">{piece.public_code}</span>
        {meta && <span>{meta}</span>}
        {availability && <span>{availability}</span>}
      </p>
      {actions}
    </article>
  );
}
