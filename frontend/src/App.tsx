import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, type AppState, type FormatPolicy, type Item, type Settings } from "./api";
import BottomBar from "./components/BottomBar";
import FormatModal from "./components/FormatModal";
import Inspector from "./components/Inspector";
import QueueRail from "./components/QueueRail";
import Stage from "./components/Stage";
import { toRegion, type Rect } from "./regionEditor";
import { initialModel, needsFormatChoice, reducer } from "./state";
import { useServerEvents } from "./useServerEvents";

type Drawer = "queue" | "inspector" | null;

export default function App() {
  const [model, dispatch] = useReducer(reducer, initialModel);
  useServerEvents(dispatch);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [status, setStatusText] = useState("");
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [formatOpen, setFormatOpen] = useState(false);
  const [dropping, setDropping] = useState(false);
  const statusTimer = useRef<number | undefined>(undefined);

  const app = model.app;
  const items = app?.items ?? [];
  const selected = items.find((i) => i.id === selectedId) ?? items[0] ?? null;

  const setStatus = useCallback((message: string) => {
    window.clearTimeout(statusTimer.current);
    setStatusText(message);
    if (message) statusTimer.current = window.setTimeout(() => setStatusText(""), 7000);
  }, []);

  // Run an API call; show the server's message on failure.
  const attempt = useCallback(
    async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
      try {
        return await fn();
      } catch (err) {
        setStatus(err instanceof Error ? err.message : "Something went wrong.");
        return undefined;
      }
    },
    [setStatus],
  );

  const applyState = useCallback(
    (state: AppState | undefined) => state && dispatch({ type: "state", state }),
    [],
  );

  const addVideos = () => void attempt(api.pickVideos).then(applyState);

  const upload = (files: File[]) => {
    if (!files.length) return;
    setStatus(`Adding ${files.length} ${files.length === 1 ? "video" : "videos"}.`);
    void attempt(() => api.upload(files)).then((s) => {
      applyState(s);
      if (s) setStatus("");
    });
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDropping(false);
    upload(Array.from(e.dataTransfer.files));
  };
  const hasFiles = (e: React.DragEvent) => e.dataTransfer.types.includes("Files");

  const setRegion = useCallback(
    async (item: Item, rect: Rect | null) => {
      const region = rect ? toRegion(rect, { w: item.width, h: item.height }) : null;
      const updated = await attempt(() => api.setRegion(item.id, region));
      if (updated) dispatch({ type: "item", item: updated });
    },
    [attempt],
  );

  const applyAll = async (item: Item) => {
    const res = await attempt(() => api.applyAll(item.id));
    if (!res) return;
    applyState(res.state);
    setStatus(
      res.skipped
        ? `Region applied. ${res.skipped} ${res.skipped === 1 ? "video was" : "videos were"} too small.`
        : "Region applied to all videos.",
    );
  };

  const changeSettings = (patch: Partial<Settings>) => {
    dispatch({ type: "settings", settings: patch });
    void attempt(() => api.updateSettings(patch));
  };

  const pickFolder = async () => {
    const s = await attempt(api.pickOutputFolder);
    if (s) dispatch({ type: "settings", settings: s });
  };

  const start = async (policy?: FormatPolicy) => {
    setFormatOpen(false);
    applyState(await attempt(() => api.start(policy)));
  };

  const copyDiagnostics = async () => {
    const d = await attempt(api.diagnostics);
    if (!d) return;
    try {
      await navigator.clipboard.writeText(d.text);
      setStatus("Diagnostics copied.");
    } catch {
      setStatus("Could not copy. Allow clipboard access and try again.");
    }
  };

  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer]);

  const select = (id: string) => {
    setSelectedId(id);
    setDrawer(null);
  };

  return (
    <div
      className="app"
      onDragOver={(e) => {
        if (hasFiles(e)) {
          e.preventDefault();
          setDropping(true);
        }
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDropping(false);
      }}
      onDrop={onDrop}
    >
      <header className="topbar">
        <img src="/favicon.svg" alt="" width="28" height="28" />
        <h1>PyWatermarkCleaner</h1>
        <span className="grow" />
        {!model.connected && app && (
          <span className="offline" role="status">
            Reconnecting...
          </span>
        )}
        {items.length > 0 && (
          <>
            <button type="button" className="btn narrow-only" onClick={() => setDrawer("queue")}>
              Queue ({items.length})
            </button>
            <button type="button" className="btn narrow-only" onClick={() => setDrawer("inspector")}>
              Settings
            </button>
            <button type="button" className="btn" onClick={addVideos}>
              Add videos
            </button>
          </>
        )}
      </header>

      {!app ? (
        <main className="center-msg" role="status">
          {model.connected ? "Loading..." : "Connecting to the local server..."}
        </main>
      ) : items.length === 0 ? (
        <main className="empty">
          <div className="dropzone" data-active={dropping}>
            <img src="/favicon.svg" alt="" width="72" height="72" />
            <h2>Drop videos here</h2>
            <p>MP4, MOV, MKV, AVI and WebM. Everything stays on this computer.</p>
            <button type="button" className="btn btn-primary btn-large" onClick={addVideos}>
              Add videos
            </button>
          </div>
        </main>
      ) : (
        <>
          <main className="workspace" data-drawer={drawer ?? undefined}>
            <button
              type="button"
              className="scrim"
              aria-label="Close panel"
              tabIndex={-1}
              onClick={() => setDrawer(null)}
            />
            <aside className="rail rail-queue" aria-label="Queue">
              <QueueRail
                items={items}
                selectedId={selected?.id ?? null}
                onSelect={select}
                onRemove={(i) => void attempt(() => api.remove(i.id)).then(applyState)}
                onCancel={(i) =>
                  void attempt(() => api.cancel(i.id)).then(
                    (item) => item && dispatch({ type: "item", item }),
                  )
                }
                onRetry={(i) =>
                  void attempt(() => api.retry(i.id)).then(
                    (item) => item && dispatch({ type: "item", item }),
                  )
                }
                onReveal={(i) => void attempt(() => api.reveal(i.id))}
              />
            </aside>
            {selected && (
              <Stage
                key={selected.id}
                item={selected}
                settings={app.settings}
                onRegion={setRegion}
                onStatus={setStatus}
              />
            )}
            <aside className="rail rail-inspector" aria-label="Settings">
              <Inspector
                item={selected}
                settings={app.settings}
                maxWorkers={app.max_workers}
                locked={app.running}
                onRegion={(i, r) => void setRegion(i, r)}
                onApplyAll={(i) => void applyAll(i)}
                onSettings={changeSettings}
                onPickFolder={() => void pickFolder()}
              />
            </aside>
          </main>
          <BottomBar
            items={items}
            running={app.running}
            progress={app.batch_progress}
            log={model.log}
            onStart={() => (needsFormatChoice(items) ? setFormatOpen(true) : void start())}
            onCancelAll={() => void attempt(api.cancelAll).then(applyState)}
            onCopyDiagnostics={() => void copyDiagnostics()}
          />
        </>
      )}

      {formatOpen && <FormatModal onChoose={(p) => void start(p)} onClose={() => setFormatOpen(false)} />}
      <div className="toast" role="status" aria-live="polite">
        {status}
      </div>
      {dropping && items.length > 0 && <div className="drop-veil">Drop to add videos</div>}
    </div>
  );
}
