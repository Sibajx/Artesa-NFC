// Hero "field of threads": drifting points that link into a constellation
// over the hero image, like threads of a loom made of light. Decorative only
// (the canvas is aria-hidden). It never runs with prefers-reduced-motion, it
// pauses while the hero is off-screen or the tab is hidden, and it caps the
// device-pixel ratio and point count so phones stay smooth.

interface Point {
  x: number;
  y: number;
  vx: number;
  vy: number;
  hue: 0 | 1; // 0 = cempasúchil, 1 = añil
}

const COLORS = ["242, 163, 58", "120, 136, 240"] as const;
const LINK_DISTANCE = 130;
const POINTER_RADIUS = 180;

export function startHeroField(canvas: HTMLCanvasElement): void {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const context = canvas.getContext("2d");
  if (!context) return;
  const ctx: CanvasRenderingContext2D = context;

  let width = 0;
  let height = 0;
  let points: Point[] = [];
  let running = false;
  let visible = true;
  let frame = 0;
  const pointer = { x: -9999, y: -9999 };

  function resize(): void {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = canvas.clientWidth;
    height = canvas.clientHeight;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // ~1 point per 14 000 px², capped: ~45 on a phone, ~110 on desktop.
    const count = Math.min(110, Math.max(36, Math.round((width * height) / 14000)));
    points = Array.from({ length: count }, (_, i) => ({
      x: Math.random() * width,
      y: Math.random() * height,
      vx: (Math.random() - 0.5) * 0.28,
      vy: (Math.random() - 0.5) * 0.28,
      hue: i % 5 === 0 ? 1 : 0,
    }));
  }

  function step(): void {
    ctx.clearRect(0, 0, width, height);
    for (const p of points) {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0 || p.x > width) p.vx *= -1;
      if (p.y < 0 || p.y > height) p.vy *= -1;
      // Gentle pull toward the pointer: the visitor "touches" the weave.
      const dx = pointer.x - p.x;
      const dy = pointer.y - p.y;
      const dist = Math.hypot(dx, dy);
      if (dist < POINTER_RADIUS && dist > 1) {
        p.x += (dx / dist) * 0.35;
        p.y += (dy / dist) * 0.35;
      }
    }
    for (let i = 0; i < points.length; i++) {
      const a = points[i];
      if (!a) continue;
      for (let j = i + 1; j < points.length; j++) {
        const b = points[j];
        if (!b) continue;
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < LINK_DISTANCE) {
          ctx.strokeStyle = `rgba(${COLORS[a.hue]}, ${(1 - d / LINK_DISTANCE) * 0.32})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }
    for (const p of points) {
      ctx.fillStyle = `rgba(${COLORS[p.hue]}, 0.85)`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.hue ? 1.6 : 1.2, 0, Math.PI * 2);
      ctx.fill();
    }
    frame = requestAnimationFrame(step);
  }

  function sync(): void {
    const shouldRun = visible && !document.hidden;
    if (shouldRun && !running) {
      running = true;
      frame = requestAnimationFrame(step);
    } else if (!shouldRun && running) {
      running = false;
      cancelAnimationFrame(frame);
    }
  }

  resize();
  canvas.classList.add("is-live");

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(resize, 150);
  });
  const host = canvas.closest("section") ?? canvas;
  host.addEventListener(
    "pointermove",
    (e) => {
      const rect = canvas.getBoundingClientRect();
      pointer.x = e.clientX - rect.left;
      pointer.y = e.clientY - rect.top;
    },
    { passive: true },
  );
  host.addEventListener("pointerleave", () => {
    pointer.x = pointer.y = -9999;
  });
  document.addEventListener("visibilitychange", sync);
  new IntersectionObserver(([entry]) => {
    visible = Boolean(entry?.isIntersecting);
    sync();
  }).observe(canvas);
  sync();
}
