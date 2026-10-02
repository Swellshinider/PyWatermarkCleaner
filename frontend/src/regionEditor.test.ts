import { describe, expect, it } from "vitest";
import {
  beginDrag,
  cancelDrag,
  clampRect,
  fromRegion,
  hitTest,
  letterbox,
  nudge,
  screenToSource,
  sourceToScreen,
  toRegion,
  updateDrag,
  type DragMode,
} from "./regionEditor";

const src = { w: 200, h: 100 };

describe("letterbox mapping", () => {
  it("centres a wide frame in a tall container", () => {
    const lb = letterbox({ w: 100, h: 100 }, src);
    expect(lb).toEqual({ x: 0, y: 25, w: 100, h: 50, scale: 0.5 });
  });
  it("round-trips screen and source points and clamps outside the frame", () => {
    const lb = letterbox({ w: 100, h: 100 }, src);
    const p = screenToSource({ x: 50, y: 50 }, lb, src);
    expect(p).toEqual({ x: 100, y: 50 });
    expect(sourceToScreen(p, lb)).toEqual({ x: 50, y: 50 });
    expect(screenToSource({ x: -10, y: 500 }, lb, src)).toEqual({ x: 0, y: 100 });
  });
});

describe("drawing", () => {
  it("draws in any direction", () => {
    const d = beginDrag("draw", { x: 60, y: 40 }, null);
    expect(updateDrag(d, { x: 20, y: 10 }, src)).toEqual({ x: 20, y: 10, w: 40, h: 30 });
  });
  it("clamps to at least 2x2", () => {
    const d = beginDrag("draw", { x: 10, y: 10 }, null);
    expect(updateDrag(d, { x: 10, y: 10 }, src)).toEqual({ x: 10, y: 10, w: 2, h: 2 });
  });
});

describe("move and resize", () => {
  const r = { x: 50, y: 20, w: 40, h: 30 };
  it("moves and stays inside the frame", () => {
    const d = beginDrag("move", { x: 60, y: 30 }, r);
    expect(updateDrag(d, { x: 70, y: 35 }, src)).toEqual({ x: 60, y: 25, w: 40, h: 30 });
    expect(updateDrag(d, { x: 999, y: 999 }, src)).toEqual({ x: 160, y: 70, w: 40, h: 30 });
  });
  it("resizes from each side and corner", () => {
    const at = (mode: DragMode, to: { x: number; y: number }) =>
      updateDrag(beginDrag(mode, { x: 0, y: 0 }, r), to, src);
    expect(at("e", { x: 10, y: 99 })).toEqual({ x: 50, y: 20, w: 50, h: 30 });
    expect(at("w", { x: 10, y: 0 })).toEqual({ x: 60, y: 20, w: 30, h: 30 });
    expect(at("n", { x: 0, y: -5 })).toEqual({ x: 50, y: 15, w: 40, h: 35 });
    expect(at("se", { x: 5, y: 5 })).toEqual({ x: 50, y: 20, w: 45, h: 35 });
    expect(at("nw", { x: 5, y: 5 })).toEqual({ x: 55, y: 25, w: 35, h: 25 });
  });
  it("never shrinks below 2x2", () => {
    const d = beginDrag("e", { x: 0, y: 0 }, r);
    expect(updateDrag(d, { x: -500, y: 0 }, src).w).toBe(2);
  });
  it("cancel returns the original rect", () => {
    expect(cancelDrag(beginDrag("move", { x: 0, y: 0 }, r))).toBe(r);
    expect(cancelDrag(beginDrag("draw", { x: 0, y: 0 }, null))).toBeNull();
  });
});

describe("hitTest", () => {
  const r = { x: 50, y: 20, w: 40, h: 30 };
  it("finds handles, body and outside", () => {
    expect(hitTest(r, { x: 50, y: 20 }, 3)).toBe("nw");
    expect(hitTest(r, { x: 70, y: 50 }, 3)).toBe("s");
    expect(hitTest(r, { x: 90, y: 35 }, 3)).toBe("e");
    expect(hitTest(r, { x: 70, y: 35 }, 3)).toBe("move");
    expect(hitTest(r, { x: 0, y: 0 }, 3)).toBeNull();
    expect(hitTest(null, { x: 0, y: 0 }, 3)).toBeNull();
  });
});

describe("nudge", () => {
  const r = { x: 10, y: 10, w: 20, h: 20 };
  it("moves 1px, or 10px with shift", () => {
    expect(nudge(r, "ArrowRight", false, src)).toEqual({ ...r, x: 11 });
    expect(nudge(r, "ArrowUp", true, src)).toEqual({ ...r, y: 0 });
    expect(nudge(r, "ArrowLeft", true, src)).toEqual({ ...r, x: 0 });
  });
  it("ignores other keys", () => {
    expect(nudge(r, "a", false, src)).toBeNull();
  });
});

describe("normalization", () => {
  it("round-trips and clamps", () => {
    const r = { x: 20, y: 10, w: 40, h: 30 };
    expect(toRegion(r, src)).toEqual({ x: 0.1, y: 0.1, width: 0.2, height: 0.3 });
    expect(fromRegion(toRegion(r, src), src)).toEqual(r);
    expect(clampRect({ x: -5, y: 0, w: 1, h: 500 }, src)).toEqual({ x: 0, y: 0, w: 2, h: 100 });
  });
});
