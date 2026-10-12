import { describe, expect, it } from "vitest";
import { REST, clampUnit, orientationTilt, pointerTilt } from "../../src/lib/tilt";

const box = { left: 100, top: 200, width: 400, height: 600 };

describe("tilt of the original certificate", () => {
  it("clamps to -1..1 and ignores values that are not numbers", () => {
    expect(clampUnit(3)).toBe(1);
    expect(clampUnit(-3)).toBe(-1);
    expect(clampUnit(0.25)).toBe(0.25);
    expect(clampUnit(Number.NaN)).toBe(0);
    expect(clampUnit(Number.POSITIVE_INFINITY)).toBe(0);
  });

  it("reads a pointer as 0 in the middle of the box and +-1 at its edges", () => {
    expect(pointerTilt(300, 500, box)).toEqual({ x: 0, y: 0 });
    expect(pointerTilt(100, 200, box)).toEqual({ x: -1, y: -1 });
    expect(pointerTilt(500, 800, box)).toEqual({ x: 1, y: 1 });
  });

  it("stays inside -1..1 when the pointer leaves the box", () => {
    expect(pointerTilt(5000, -5000, box)).toEqual({ x: 1, y: -1 });
  });

  it("rests when the box has no size", () => {
    expect(pointerTilt(10, 10, { left: 0, top: 0, width: 0, height: 0 })).toBe(REST);
  });

  it("reads a phone held to read (45 degrees, level) as rest", () => {
    expect(orientationTilt(45, 0)).toEqual({ x: 0, y: 0 });
  });

  it("tilts with the lean of the phone and clamps at the range", () => {
    expect(orientationTilt(45, 14)).toEqual({ x: 0.5, y: 0 });
    expect(orientationTilt(73, -28)).toEqual({ x: -1, y: 1 });
    expect(orientationTilt(180, 90)).toEqual({ x: 1, y: 1 });
  });

  it("rests when the phone reports nothing", () => {
    expect(orientationTilt(null, 10)).toBe(REST);
    expect(orientationTilt(10, null)).toBe(REST);
    expect(orientationTilt(10, 10, 0)).toBe(REST);
  });
});
