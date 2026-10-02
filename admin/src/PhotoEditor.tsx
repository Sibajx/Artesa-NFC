import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';

// Crop / rotate a photo in the browser before it is uploaded. The result is a
// new JPEG; the server still re-encodes it (EXIF and GPS out, ≤ 1600 px).
// Pan by dragging the photo or with the arrow keys; zoom with the slider.

const ASPECTS: { label: string; value: number | null }[] = [
  { label: 'Original', value: null },
  { label: 'Cuadrada 1:1', value: 1 },
  { label: 'Vertical 4:5', value: 4 / 5 },
  { label: 'Vertical 3:4', value: 3 / 4 },
  { label: 'Horizontal 16:9', value: 16 / 9 },
];

const OUTPUT_MAX = 2400; // px, longest side of the exported crop
const VIEW_MAX = 520; // px, longest side of the on-screen preview

interface Props {
  source: Blob;
  onDone: (edited: Blob) => void;
  onCancel: () => void;
}

export function PhotoEditor({ source, onDone, onCancel }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [failed, setFailed] = useState(false);
  const [rotation, setRotation] = useState(0); // 0, 90, 180, 270
  const [aspect, setAspect] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 }); // fraction of the free space, -1..1
  const drag = useRef<{ x: number; y: number; pan: { x: number; y: number } } | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    dialogRef.current?.showModal();
    const url = URL.createObjectURL(source);
    const img = new Image();
    img.onload = () => setImage(img);
    img.onerror = () => setFailed(true);
    img.src = url;
    return () => URL.revokeObjectURL(url);
  }, [source]);

  // Geometry of the crop in rotated-image pixels.
  function geometry(img: HTMLImageElement) {
    const quarter = rotation % 180 !== 0;
    const rw = quarter ? img.naturalHeight : img.naturalWidth;
    const rh = quarter ? img.naturalWidth : img.naturalHeight;
    const ratio = aspect ?? rw / rh;
    let cw = rw;
    let ch = rw / ratio;
    if (ch > rh) {
      ch = rh;
      cw = rh * ratio;
    }
    cw /= zoom;
    ch /= zoom;
    const cx = (rw - cw) / 2 + (pan.x * (rw - cw)) / 2;
    const cy = (rh - ch) / 2 + (pan.y * (rh - ch)) / 2;
    return { rw, rh, cw, ch, cx, cy };
  }

  // Draws the crop of the rotated image into `canvas` at `scale`.
  function draw(canvas: HTMLCanvasElement, img: HTMLImageElement, maxSide: number) {
    const { rw, rh, cw, ch, cx, cy } = geometry(img);
    const scale = Math.min(1, maxSide / Math.max(cw, ch));
    canvas.width = Math.max(1, Math.round(cw * scale));
    canvas.height = Math.max(1, Math.round(ch * scale));
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.save();
    ctx.scale(scale, scale);
    ctx.translate(-cx, -cy);
    // Rotate the whole image about the rotated frame's centre.
    ctx.translate(rw / 2, rh / 2);
    ctx.rotate((rotation * Math.PI) / 180);
    ctx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);
    ctx.restore();
  }

  useEffect(() => {
    if (image && canvasRef.current) draw(canvasRef.current, image, VIEW_MAX);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [image, rotation, aspect, zoom, pan]);

  const clamp = (v: number) => Math.max(-1, Math.min(1, v));

  function onPointerDown(e: PointerEvent<HTMLCanvasElement>) {
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, pan };
  }

  function onPointerMove(e: PointerEvent<HTMLCanvasElement>) {
    if (!drag.current) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const dx = (e.clientX - drag.current.x) / rect.width;
    const dy = (e.clientY - drag.current.y) / rect.height;
    // Dragging the photo right shows more of its left side.
    setPan({ x: clamp(drag.current.pan.x - dx * 2), y: clamp(drag.current.pan.y - dy * 2) });
  }

  function onKey(e: KeyboardEvent<HTMLCanvasElement>) {
    const step = 0.1;
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step],
    };
    const move = moves[e.key];
    if (!move) return;
    e.preventDefault();
    setPan((p) => ({ x: clamp(p.x + move[0]), y: clamp(p.y + move[1]) }));
  }

  function save() {
    if (!image) return;
    setSaving(true);
    const out = document.createElement('canvas');
    draw(out, image, OUTPUT_MAX);
    out.toBlob((blob) => {
      setSaving(false);
      if (blob) onDone(blob);
    }, 'image/jpeg', 0.92);
  }

  return (
    <dialog ref={dialogRef} onCancel={(e) => { e.preventDefault(); onCancel(); }}
      aria-labelledby="photo-editor-title"
      className="m-auto rounded-xl p-0 backdrop:bg-black/50 max-w-[min(92vw,640px)] w-full">
      <div className="p-6 flex flex-col gap-4">
        <h2 id="photo-editor-title" className="text-xl font-serif text-botanica-negro">Recortar y rotar</h2>
        {failed && <p role="alert" className="text-sm text-red-700">No se pudo abrir esta imagen en el navegador.</p>}
        <div className="flex justify-center bg-botanica-hueso rounded-md p-2">
          <canvas ref={canvasRef} tabIndex={0} role="img"
            aria-label="Vista previa del recorte. Arrastra la foto o usa las flechas para encuadrar."
            onPointerDown={onPointerDown} onPointerMove={onPointerMove}
            onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
            onKeyDown={onKey}
            className="max-w-full max-h-[52vh] cursor-move touch-none focus:outline focus:outline-2 focus:outline-botanica-jade" />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-secondary" onClick={() => setRotation((r) => (r + 270) % 360)}>⟲ Girar a la izquierda</button>
          <button type="button" className="btn-secondary" onClick={() => setRotation((r) => (r + 90) % 360)}>⟳ Girar a la derecha</button>
        </div>
        <fieldset className="flex flex-wrap gap-2">
          <legend className="text-xs font-medium text-botanica-grafito mb-1">Proporción</legend>
          {ASPECTS.map((a) => (
            <button key={a.label} type="button" aria-pressed={aspect === a.value}
              onClick={() => { setAspect(a.value); setPan({ x: 0, y: 0 }); }}
              className={aspect === a.value ? 'btn-primary' : 'btn-secondary'}>
              {a.label}
            </button>
          ))}
        </fieldset>
        <label className="flex items-center gap-3 text-sm text-botanica-grafito">
          Acercar
          <input type="range" min={1} max={3} step={0.05} value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))} className="flex-1" />
        </label>
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={onCancel}>Cancelar</button>
          <button type="button" className="btn-primary" disabled={!image || saving} onClick={save}>
            {saving ? 'Aplicando…' : 'Usar esta foto'}
          </button>
        </div>
      </div>
    </dialog>
  );
}
