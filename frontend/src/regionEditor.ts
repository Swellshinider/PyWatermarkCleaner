// Pure region-editing logic. Rects are in integer source pixels; screen points are
// relative to the container element that holds the (letterboxed) video.

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface Point {
  x: number;
  y: number;
}
export interface Size {
  w: number;
  h: number;
}
export interface Letterbox {
  x: number;
  y: number;
  w: number;
  h: number;
  scale: number;
}
export type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
export type DragMode = "draw" | "move" | Handle;
export interface Drag {
  mode: DragMode;
  start: Point; // source px where the pointer went down
  original: Rect | null;
}

export const MIN_SIZE = 2;
export const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Where the source frame sits inside the container (object-fit: contain). */
export function letterbox(container: Size, source: Size): Letterbox {
  if (container.w <= 0 || container.h <= 0 || source.w <= 0 || source.h <= 0) {
    return { x: 0, y: 0, w: 0, h: 0, scale: 1 };
  }
  const scale = Math.min(container.w / source.w, container.h / source.h);
  const w = source.w * scale;
  const h = source.h * scale;
  return { x: (container.w - w) / 2, y: (container.h - h) / 2, w, h, scale };
}

export function screenToSource(p: Point, lb: Letterbox, source: Size): Point {
  return {
    x: clamp((p.x - lb.x) / lb.scale, 0, source.w),
    y: clamp((p.y - lb.y) / lb.scale, 0, source.h),
  };
}

export function sourceToScreen(p: Point, lb: Letterbox): Point {
  return { x: lb.x + p.x * lb.scale, y: lb.y + p.y * lb.scale };
}

/** Round, enforce min size, and keep the rect inside the frame. */
export function clampRect(r: Rect, source: Size): Rect {
  const w = clamp(Math.round(r.w), MIN_SIZE, source.w);
  const h = clamp(Math.round(r.h), MIN_SIZE, source.h);
  return {
    w,
    h,
    x: clamp(Math.round(r.x), 0, source.w - w),
    y: clamp(Math.round(r.y), 0, source.h - h),
  };
}

const rectFromPoints = (a: Point, b: Point): Rect => ({
  x: Math.min(a.x, b.x),
  y: Math.min(a.y, b.y),
  w: Math.abs(a.x - b.x),
  h: Math.abs(a.y - b.y),
});

/** Which handle (or the body) is under `p`. `tol` is the grab radius in source px. */
export function hitTest(rect: Rect | null, p: Point, tol: number): Handle | "move" | null {
  if (!rect) return null;
  const { x, y, w, h } = rect;
  const xs: [number, string][] = [
    [x, "w"],
    [x + w / 2, ""],
    [x + w, "e"],
  ];
  const ys: [number, string][] = [
    [y, "n"],
    [y + h / 2, ""],
    [y + h, "s"],
  ];
  for (const [hy, vy] of ys) {
    for (const [hx, vx] of xs) {
      if (!vx && !vy) continue;
      if (Math.abs(p.x - hx) <= tol && Math.abs(p.y - hy) <= tol) return (vy + vx) as Handle;
    }
  }
  if (p.x >= x && p.x <= x + w && p.y >= y && p.y <= y + h) return "move";
  return null;
}

export function beginDrag(mode: DragMode, start: Point, original: Rect | null): Drag {
  return { mode, start, original };
}

/** Rect for the current pointer position; always valid (clamped, >= 2x2). */
export function updateDrag(drag: Drag, p: Point, source: Size): Rect {
  const o = drag.original;
  if (drag.mode === "draw" || !o) {
    return clampRect(rectFromPoints(drag.start, p), source);
  }
  const dx = p.x - drag.start.x;
  const dy = p.y - drag.start.y;
  if (drag.mode === "move") {
    return clampRect({ ...o, x: o.x + dx, y: o.y + dy }, source);
  }
  let left = o.x;
  let top = o.y;
  let right = o.x + o.w;
  let bottom = o.y + o.h;
  const m = drag.mode;
  if (m.includes("w")) left = clamp(left + dx, 0, right - MIN_SIZE);
  if (m.includes("e")) right = clamp(right + dx, left + MIN_SIZE, source.w);
  if (m.includes("n")) top = clamp(top + dy, 0, bottom - MIN_SIZE);
  if (m.includes("s")) bottom = clamp(bottom + dy, top + MIN_SIZE, source.h);
  return clampRect({ x: left, y: top, w: right - left, h: bottom - top }, source);
}

/** Escape: the rect to restore (null when the drag started from no region). */
export function cancelDrag(drag: Drag): Rect | null {
  return drag.original;
}

const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

/** Arrow-key nudge (Shift = 10px). Returns null for non-arrow keys. */
export function nudge(rect: Rect, key: string, shift: boolean, source: Size): Rect | null {
  const d = ARROWS[key];
  if (!d) return null;
  const step = shift ? 10 : 1;
  return clampRect({ ...rect, x: rect.x + d[0] * step, y: rect.y + d[1] * step }, source);
}

export interface NormalizedRegion {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const toRegion = (r: Rect, s: Size): NormalizedRegion => ({
  x: r.x / s.w,
  y: r.y / s.h,
  width: r.w / s.w,
  height: r.h / s.h,
});

export const fromRegion = (r: NormalizedRegion, s: Size): Rect =>
  clampRect({ x: r.x * s.w, y: r.y * s.h, w: r.width * s.w, h: r.height * s.h }, s);
