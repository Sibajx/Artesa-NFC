// Pure maths for the tilt of the original certificate (kept apart so it is tested without a browser).
// Both helpers answer in -1..1 on each axis: x is left to right, y is top to bottom.

export interface Tilt {
  readonly x: number;
  readonly y: number;
}

export const REST: Tilt = { x: 0, y: 0 };

export const clampUnit = (value: number): number =>
  Number.isFinite(value) ? Math.max(-1, Math.min(1, value)) : 0;

/** Where a pointer is on a box: the centre is 0, the edges are +-1. */
export function pointerTilt(
  clientX: number,
  clientY: number,
  box: { left: number; top: number; width: number; height: number },
): Tilt {
  if (box.width <= 0 || box.height <= 0) return REST;
  return {
    x: clampUnit(((clientX - box.left) / box.width) * 2 - 1),
    y: clampUnit(((clientY - box.top) / box.height) * 2 - 1),
  };
}

/**
 * How a phone is held. `gamma` is its left-right lean and `beta` its front-back lean, in degrees.
 * A phone held to read sits near 45 degrees front-back, so that is the rest position; a lean of
 * `range` degrees from rest is the full tilt.
 */
export function orientationTilt(
  beta: number | null,
  gamma: number | null,
  range = 28,
  restBeta = 45,
): Tilt {
  if (beta === null || gamma === null || range <= 0) return REST;
  return { x: clampUnit(gamma / range), y: clampUnit((beta - restBeta) / range) };
}
