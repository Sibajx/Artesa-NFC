// Counts from 0 to `target` once, after mount (the counters live in the page
// hero, always on screen at load). Decorative: the final number is exposed
// to assistive tech by the caller, and reduced motion shows it at once.
import { useEffect, useState } from "react";

export function useCountUp(target: number, durationMs = 900): number {
  const reduced =
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [value, setValue] = useState(reduced ? target : 0);

  useEffect(() => {
    if (reduced) return;
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      setValue(Math.round(target * (1 - Math.pow(1 - t, 3))));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, durationMs, reduced]);

  return value;
}
