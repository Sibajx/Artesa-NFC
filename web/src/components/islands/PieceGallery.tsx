// /piezas — the collection. Photographs first; no GLB and no 3D code is
// downloaded on entry. The list contract (API_CONTRACT.md §8) does not say
// which pieces have a model, so each card, once near the viewport, asks for
// its piece detail (small JSON) to learn that. A "Ver en 3D" action then
// opens the viewer in a dialog, loading the model only on request.
// Contract proposal to avoid these extra requests: Artesa_Brain,
// "Propuesta: indicador 3D en el listado".
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { displayName } from "@/lib/format";
import { pickHeroImage, pickModel } from "@/lib/media";
import { piecePath } from "@/lib/routes";
import type { MediaAsset, Piece, PieceSummary } from "@/lib/types";
import { PieceCard } from "./PieceCard";
import { PieceViewer } from "./PieceViewer";
import { LoadingView, UnavailableView } from "./StatusView";
import { useApi } from "./useApi";
import { useInView } from "./useInView";

export default function PieceGallery() {
  const { state, retry } = useApi(() => api().getPieces());
  const [viewing, setViewing] = useState<{ piece: Piece; model: MediaAsset } | null>(null);

  if (state.kind === "loading") return <LoadingView label="Cargando piezas…" />;
  if (state.kind !== "ok") {
    return (
      <UnavailableView
        what="la colección"
        reason={state.kind === "unavailable" ? state.reason : "http_error"}
        onRetry={retry}
        level={2}
      />
    );
  }
  const pieces = state.data.data;
  if (pieces.length === 0) {
    return (
      <div className="status-view" data-state="empty">
        <p className="lead muted">Todavía no hay piezas publicadas.</p>
      </div>
    );
  }
  return (
    <>
      <p className="collection-count muted" role="status">
        {pieces.length === 1 ? "1 pieza" : `${pieces.length} piezas`}
      </p>
      <ul className="piece-grid" role="list">
        {pieces.map((piece) => (
          <li key={piece.slug}>
            <GalleryCard
              piece={piece}
              onView3D={(detail, model) => setViewing({ piece: detail, model })}
            />
          </li>
        ))}
      </ul>
      {viewing && (
        <ViewerDialog
          piece={viewing.piece}
          model={viewing.model}
          onClose={() => setViewing(null)}
        />
      )}
    </>
  );
}

function GalleryCard({
  piece,
  onView3D,
}: {
  piece: PieceSummary;
  onView3D: (piece: Piece, model: MediaAsset) => void;
}) {
  const { ref, inView } = useInView<HTMLDivElement>();
  const [detail, setDetail] = useState<Piece | null>(null);

  useEffect(() => {
    if (!inView) return;
    let active = true;
    void api()
      .getPiece(piece.slug)
      .then((result) => {
        if (active && result.kind === "ok") setDetail(result.data);
      });
    return () => {
      active = false;
    };
  }, [inView, piece.slug]);

  const model = detail ? pickModel(detail.media) : null;
  return (
    <div ref={ref}>
      <PieceCard
        piece={piece}
        meta={detail ? displayName(detail.artisan) : null}
        badge={
          model ? (
            <span className="piece-card__badge" aria-hidden="true">
              3D
            </span>
          ) : null
        }
        actions={
          model && detail ? (
            <button
              type="button"
              className="editorial-link editorial-link--plain piece-card__3d"
              aria-label={`Ver en 3D: ${piece.name}`}
              onClick={() => onView3D(detail, model)}
            >
              Ver en 3D
            </button>
          ) : null
        }
      />
    </div>
  );
}

function ViewerDialog({
  piece,
  model,
  onClose,
}: {
  piece: Piece;
  model: MediaAsset;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    opener.current = document.activeElement;
    dialog.current?.showModal();
    const returnTo = opener.current;
    return () => {
      if (returnTo instanceof HTMLElement) returnTo.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialog}
      className="viewer-dialog"
      aria-labelledby="viewer-dialog-title"
      onClose={onClose}
    >
      <div className="viewer-dialog__header">
        <h2 id="viewer-dialog-title" className="heading-2">
          {piece.name}
        </h2>
        <button type="button" className="button" onClick={() => dialog.current?.close()}>
          Cerrar
        </button>
      </div>
      <PieceViewer model={model} poster={pickHeroImage(piece.media)} name={piece.name} autoLoad />
      <a className="editorial-link" href={piecePath(piece.slug)}>
        Ver la ficha completa
      </a>
    </dialog>
  );
}
