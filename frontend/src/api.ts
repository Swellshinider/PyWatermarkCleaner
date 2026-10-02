// Types mirror the backend contract (snake_case JSON).
export interface Region {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type ItemState =
  | "needs_region"
  | "ready"
  | "queued"
  | "processing"
  | "completed"
  | "failed"
  | "canceled";

export interface Item {
  id: string;
  name: string;
  width: number;
  height: number;
  fps: number;
  duration_ms: number;
  container: string;
  state: ItemState;
  progress: number;
  region: Region | null;
  output_path: string | null;
  error: string;
  needs_proxy: boolean;
}

export type Method = "telea" | "navier-stokes";
export type Performance = "fast" | "balanced" | "quality";
export type FormatPolicy = "original" | "mp4";

export interface Settings {
  method: Method;
  radius: number;
  performance: Performance;
  workers: number;
  output_folder: string;
  format_policy: FormatPolicy;
}

export interface AppState {
  items: Item[];
  settings: Settings;
  max_workers: number;
  running: boolean;
  batch_progress: number;
  version: string;
}

export type ServerEvent =
  | { type: "state"; state: AppState }
  | { type: "item"; item: Item }
  | { type: "log"; message: string };

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin" };
  if (body instanceof Blob) {
    init.body = body; // raw upload: the browser streams the file from disk
  } else if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail || `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

export const api = {
  state: () => request<AppState>("GET", "/state"),
  pickVideos: () => request<AppState>("POST", "/videos/pick"),
  upload: (file: File) =>
    request<AppState>("POST", `/videos/upload?name=${encodeURIComponent(file.name)}`, file),
  remove: (id: string) => request<AppState>("DELETE", `/videos/${id}`),
  setRegion: (id: string, region: Region | null) =>
    request<Item>("PUT", `/videos/${id}/region`, region),
  applyAll: (sourceId: string) =>
    request<{ skipped: number; state: AppState }>("POST", "/region/apply-all", {
      source_id: sourceId,
    }),
  updateSettings: (patch: Partial<Settings>) => request<Settings>("PUT", "/settings", patch),
  pickOutputFolder: () => request<Settings>("POST", "/output-folder/pick"),
  start: (formatPolicy?: FormatPolicy) =>
    request<AppState>("POST", "/jobs/start", formatPolicy ? { format_policy: formatPolicy } : {}),
  cancel: (id: string) => request<Item>("POST", `/jobs/${id}/cancel`),
  retry: (id: string) => request<Item>("POST", `/jobs/${id}/retry`),
  cancelAll: () => request<AppState>("POST", "/jobs/cancel-all"),
  reveal: (id: string) => request<{ ok: boolean }>("POST", `/videos/${id}/reveal`),
  diagnostics: () => request<{ text: string }>("GET", "/diagnostics"),
  previewClip: (id: string, tMs: number) =>
    request<{ url: string }>("POST", `/videos/${id}/preview-clip?t=${Math.round(tMs)}`),
};

export const sourceUrl = (id: string) => `/api/videos/${id}/source`;
export const proxyUrl = (id: string) => `/api/videos/${id}/proxy`;
export const frameUrl = (id: string, tMs: number, cleaned: boolean) =>
  `/api/videos/${id}/frame?t=${Math.round(tMs)}&cleaned=${cleaned ? 1 : 0}`;
