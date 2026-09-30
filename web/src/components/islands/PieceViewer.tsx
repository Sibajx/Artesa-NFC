// PieceViewer — 3D view of a piece with <model-viewer> (DESIGN_SYSTEM.md §12).
//
// - Starts as the piece photograph with an explicit "Ver en 3D" action that
//   states the download size. Nothing 3D (no model-viewer, no three.js, no
//   GLB) is fetched until the visitor asks, so it never blocks navigation.
// - Drag / swipe rotates, wheel / pinch zooms, arrow keys rotate when the
//   viewer has focus. touch-action="pan-y" keeps vertical page scroll.
// - No auto-rotation. Any failure falls back to the photograph.
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { currentApiConfig, resolveMediaUrl } from "@/lib/api-config";
import { formatBytes } from "@/lib/format";
import type { MediaAsset } from "@/lib/types";
import { MediaImage } from "./MediaImage";

type ViewerState = "idle" | "loading" | "ready" | "error";

interface ModelViewerElement extends HTMLElement {
  cameraOrbit: string;
  fieldOfView: string;
  jumpCameraToGoal(): void;
}

declare module "react" {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX {
    interface IntrinsicElements {
      "model-viewer": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        src?: string;
        poster?: string | undefined;
        alt?: string;
        "camera-controls"?: boolean | "";
        "touch-action"?: string;
        "interaction-prompt"?: string;
        "shadow-intensity"?: string;
        exposure?: string;
        loading?: string;
        reveal?: string;
        "camera-orbit"?: string;
        "field-of-view"?: string;
        "min-camera-orbit"?: string;
        "max-camera-orbit"?: string;
        "environment-image"?: string;
        "tone-mapping"?: string;
      };
    }
  }
}

let modelViewerModule: Promise<unknown> | null = null;
// Loaded once, on demand; bundles three.js into its own lazy chunk. The Draco
// and KTX2 decoders (only fetched for compressed models) are served from this
// site (scripts/copy-decoders.mjs), not www.gstatic.com: the CSP allows no
// third-party origin.
export function loadModelViewer(): Promise<unknown> {
  modelViewerModule ??= import("@google/model-viewer").then((mod) => {
    mod.ModelViewerElement.dracoDecoderLocation = "/decoders/draco/";
    mod.ModelViewerElement.ktx2TranscoderLocation = "/decoders/basis/";
    return mod;
  });
  return modelViewerModule;
}

const INITIAL_ORBIT = "0deg 80deg auto";

interface Props {
  model: MediaAsset;
  poster: MediaAsset | null;
  name: string;
  /** Start loading immediately (the visitor already asked, e.g. in a dialog). */
  autoLoad?: boolean;
}

export function PieceViewer({ model, poster, name, autoLoad = false }: Props) {
  const config = currentApiConfig();
  const modelUrl = resolveMediaUrl(model.url, config);
  const posterUrl = poster ? resolveMediaUrl(poster.url, config) : null;
  const size = formatBytes(model.format?.file_size_bytes);

  const [state, setState] = useState<ViewerState>(autoLoad && modelUrl ? "loading" : "idle");
  const [progress, setProgress] = useState(0);
  const viewerRef = useRef<ModelViewerElement | null>(null);
  const statusId = useId();

  const start = useCallback(() => {
    if (!modelUrl) {
      setState("error");
      return;
    }
    setProgress(0);
    setState("loading");
  }, [modelUrl]);

  // Import the web component, then let the element load the GLB.
  useEffect(() => {
    if (state !== "loading") return;
    let active = true;
    loadModelViewer().catch(() => {
      if (active) setState("error");
    });
    return () => {
      active = false;
    };
  }, [state]);

  // model-viewer reports through DOM events, not React props.
  const attach = useCallback((el: HTMLElement | null) => {
    viewerRef.current = el as ModelViewerElement | null;
    if (!el) return;
    el.addEventListener("load", () => setState("ready"));
    el.addEventListener("error", () => setState("error"));
    el.addEventListener("progress", (event: Event) => {
      const detail = (event as CustomEvent<{ totalProgress?: number } | null>).detail ?? {};
      setProgress(Math.round((detail.totalProgress ?? 0) * 100));
    });
  }, []);

  const resetView = () => {
    const el = viewerRef.current;
    if (!el) return;
    el.cameraOrbit = INITIAL_ORBIT;
    el.fieldOfView = "auto";
    el.jumpCameraToGoal();
  };

  const showViewer = state === "loading" || state === "ready";

  return (
    <div className="piece-viewer" data-state={state}>
      <div className="piece-viewer__stage media-frame">
        {showViewer && modelUrl ? (
          <model-viewer
            ref={attach}
            src={modelUrl}
            poster={posterUrl ?? undefined}
            alt={`Modelo 3D de ${name}. Arrastra o usa las flechas del teclado para girarlo.`}
            camera-controls=""
            touch-action="pan-y"
            interaction-prompt="none"
            shadow-intensity="0.7"
            exposure="1"
            tone-mapping="neutral"
            camera-orbit={INITIAL_ORBIT}
            loading="eager"
            reveal="auto"
            aria-describedby={statusId}
            className="piece-viewer__model"
          />
        ) : (
          <MediaImage
            media={poster}
            fallbackAlt={name}
            loading="eager"
            className="piece-viewer__photo"
          />
        )}

        {state === "idle" && (
          <button
            type="button"
            className="piece-viewer__launch button"
            onClick={start}
            onPointerEnter={() => void loadModelViewer().catch(() => undefined)}
            onFocus={() => void loadModelViewer().catch(() => undefined)}
          >
            <span aria-hidden="true" className="piece-viewer__badge">
              3D
            </span>
            Ver en 3D{size ? ` · ${size}` : ""}
          </button>
        )}
      </div>

      <div className="piece-viewer__bar">
        <p id={statusId} className="piece-viewer__status" role="status" aria-live="polite">
          {state === "idle" &&
            `Esta pieza tiene un modelo 3D${size ? ` (${size})` : ""}. Se descarga solo si lo abres.`}
          {state === "loading" && `Cargando modelo 3D… ${progress}%`}
          {state === "ready" &&
            "Arrastra para girar, pellizca o usa la rueda para acercar. Con teclado: flechas para girar."}
          {state === "error" && "No se pudo cargar el modelo 3D. Se muestra la fotografía."}
        </p>
        {state === "ready" && (
          <button
            type="button"
            className="editorial-link editorial-link--plain"
            onClick={resetView}
          >
            Restablecer vista
          </button>
        )}
        {state === "error" && modelUrl && (
          <button type="button" className="editorial-link editorial-link--plain" onClick={start}>
            Reintentar 3D
          </button>
        )}
      </div>
    </div>
  );
}
