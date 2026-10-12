// A full-screen look at a photograph. It sits inside a `.media-frame` as an
// invisible button over the picture; the dialog shows the whole image
// (object-fit: contain), so a crop or aspect ratio chosen in Gestión can be
// judged at full size. Esc, the backdrop and "Cerrar" close it.
import { useEffect, useRef, useState } from "react";
import { resolveMediaUrl, currentApiConfig } from "@/lib/api-config";
import type { MediaAsset } from "@/lib/types";

interface Props {
  media: MediaAsset | null;
  /** Used when the asset has no alt_text. */
  fallbackAlt: string;
}

export function ZoomButton({ media, fallbackAlt }: Props) {
  const [open, setOpen] = useState(false);
  const src = media ? resolveMediaUrl(media.url, currentApiConfig()) : null;
  if (!media || !src) return null;
  const alt = media.alt_text ?? fallbackAlt;
  return (
    <>
      <button
        type="button"
        className="zoom-button"
        aria-label={`Ver a pantalla completa: ${alt}`}
        onClick={() => setOpen(true)}
      />
      {open && <ZoomDialog src={src} alt={alt} onClose={() => setOpen(false)} />}
    </>
  );
}

function ZoomDialog({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const returnTo = document.activeElement;
    dialog.current?.showModal();
    return () => {
      if (returnTo instanceof HTMLElement) returnTo.focus();
    };
  }, []);

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/click-events-have-key-events -- backdrop click is a mouse shortcut; Esc and the Cerrar button are the keyboard paths
    <dialog
      ref={dialog}
      className="zoom-dialog"
      aria-label={alt}
      onClose={onClose}
      onClick={(e) => {
        // A click on the backdrop lands on the <dialog> itself.
        if (e.target === dialog.current) dialog.current?.close();
      }}
    >
      <img src={src} alt={alt} className="zoom-dialog__img" />
      <button
        type="button"
        className="button zoom-dialog__close"
        onClick={() => dialog.current?.close()}
      >
        Cerrar
      </button>
    </dialog>
  );
}
