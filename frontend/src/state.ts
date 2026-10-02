import type { AppState, Item, ServerEvent, Settings } from "./api";

export interface Model {
  app: AppState | null;
  log: string[];
  connected: boolean;
}

export type Action =
  | { type: "server"; event: ServerEvent }
  | { type: "state"; state: AppState }
  | { type: "item"; item: Item }
  | { type: "settings"; settings: Partial<Settings> }
  | { type: "connected"; value: boolean }
  | { type: "log"; message: string };

export const initialModel: Model = { app: null, log: [], connected: false };

const LOG_LIMIT = 300;
const ACTIVE = ["queued", "processing"];
const IN_BATCH = ["queued", "processing", "completed", "failed", "canceled"];

function withItem(app: AppState, item: Item): AppState {
  const exists = app.items.some((i) => i.id === item.id);
  const items = exists ? app.items.map((i) => (i.id === item.id ? item : i)) : [...app.items, item];
  const running = items.some((i) => ACTIVE.includes(i.state));
  const batch = items.filter((i) => IN_BATCH.includes(i.state));
  const batch_progress =
    running && batch.length
      ? Math.round(batch.reduce((s, i) => s + i.progress, 0) / batch.length)
      : app.batch_progress;
  return { ...app, items, running, batch_progress };
}

export function reducer(model: Model, action: Action): Model {
  switch (action.type) {
    case "server":
      return reducer(model, toAction(action.event));
    case "state":
      return { ...model, app: action.state };
    case "item":
      return model.app ? { ...model, app: withItem(model.app, action.item) } : model;
    case "settings":
      return model.app
        ? { ...model, app: { ...model.app, settings: { ...model.app.settings, ...action.settings } } }
        : model;
    case "connected":
      return { ...model, connected: action.value };
    case "log":
      return { ...model, log: [...model.log, action.message].slice(-LOG_LIMIT) };
  }
}

function toAction(e: ServerEvent): Action {
  if (e.type === "state") return { type: "state", state: e.state };
  if (e.type === "item") return { type: "item", item: e.item };
  return { type: "log", message: e.message };
}

export function formatTime(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** AVI/WebM sources can't be written back as-is, so the batch asks about format. */
export function needsFormatChoice(items: Item[]): boolean {
  return items.some((i) => /(^|[,.])(avi|webm)\b/i.test(`${i.container},${i.name}`));
}
