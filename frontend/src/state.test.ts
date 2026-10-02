import { describe, expect, it } from "vitest";
import type { AppState, Item } from "./api";
import { formatTime, initialModel, needsFormatChoice, reducer } from "./state";

const item = (over: Partial<Item> = {}): Item => ({
  id: "a",
  name: "a.mp4",
  width: 100,
  height: 50,
  fps: 30,
  duration_ms: 1000,
  container: "mov,mp4",
  state: "ready",
  progress: 0,
  region: null,
  output_path: null,
  error: "",
  needs_proxy: false,
  ...over,
});

const app = (items: Item[]): AppState => ({
  items,
  settings: {
    method: "telea",
    radius: 3,
    performance: "balanced",
    workers: 2,
    output_folder: "",
    format_policy: "original",
  },
  max_workers: 4,
  running: false,
  batch_progress: 0,
  version: "x",
});

describe("reducer", () => {
  it("applies item events and derives batch progress", () => {
    let m = reducer(initialModel, { type: "state", state: app([item(), item({ id: "b" })]) });
    m = reducer(m, { type: "item", item: item({ state: "processing", progress: 50 }) });
    m = reducer(m, { type: "item", item: item({ id: "b", state: "queued", progress: 0 }) });
    expect(m.app?.running).toBe(true);
    expect(m.app?.batch_progress).toBe(25);
  });
  it("caps the log", () => {
    let m = initialModel;
    for (let i = 0; i < 400; i++) m = reducer(m, { type: "log", message: String(i) });
    expect(m.log).toHaveLength(300);
    expect(m.log.at(-1)).toBe("399");
  });
});

describe("helpers", () => {
  it("formats timecodes", () => {
    expect(formatTime(65_000)).toBe("01:05");
    expect(formatTime(3_725_000)).toBe("1:02:05");
  });
  it("detects avi/webm", () => {
    expect(needsFormatChoice([item()])).toBe(false);
    expect(needsFormatChoice([item({ container: "avi" })])).toBe(true);
    expect(needsFormatChoice([item({ container: "matroska,webm" })])).toBe(true);
    expect(needsFormatChoice([item({ name: "clip.WEBM", container: "" })])).toBe(true);
  });
});
