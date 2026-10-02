import { useState } from "react";
import type { Item } from "../api";

interface Props {
  items: Item[];
  running: boolean;
  progress: number;
  log: string[];
  onStart: () => void;
  onCancelAll: () => void;
  onCopyDiagnostics: () => void;
}

export default function BottomBar({
  items,
  running,
  progress,
  log,
  onStart,
  onCancelAll,
  onCopyDiagnostics,
}: Props) {
  const [logOpen, setLogOpen] = useState(false);
  const ready = items.filter((i) => i.state === "ready").length;
  const missing = items.filter((i) => i.state === "needs_region").length;
  const done = items.filter((i) => i.state === "completed").length;
  const canStart = !running && missing === 0 && ready > 0;

  let note = "";
  if (running) note = `${done} of ${items.length} done`;
  else if (missing) note = `${missing} ${missing === 1 ? "video needs" : "videos need"} a region`;
  else if (!ready && items.length) note = "Nothing left to clean";

  return (
    <footer className="bar">
      {logOpen && (
        <div className="log" role="log" aria-label="Activity log" tabIndex={0}>
          {log.length ? log.map((l, i) => <div key={i}>{l}</div>) : <div>No activity yet.</div>}
        </div>
      )}
      <div className="bar-row">
        <button type="button" className="btn btn-primary" disabled={!canStart} onClick={onStart}>
          {`Clean ${ready} ${ready === 1 ? "video" : "videos"}`}
        </button>
        <div className="bar-progress">
          {running && (
            <progress className="bar big" max={100} value={progress} aria-label="Batch progress" />
          )}
          <span className="bar-note" aria-live="polite">
            {running ? `${progress}%, ` : ""}
            {note}
          </span>
        </div>
        {running && (
          <button type="button" className="btn" onClick={onCancelAll}>
            Cancel all
          </button>
        )}
        <button
          type="button"
          className="link"
          aria-expanded={logOpen}
          onClick={() => setLogOpen((o) => !o)}
        >
          Activity log
        </button>
        <button type="button" className="link" onClick={onCopyDiagnostics}>
          Copy diagnostics
        </button>
      </div>
    </footer>
  );
}
