import { useState } from "react";
import type { Item, ItemState } from "../api";
import { formatTime } from "../state";

interface Props {
  items: Item[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRemove: (item: Item) => void;
  onCancel: (item: Item) => void;
  onRetry: (item: Item) => void;
  onReveal: (item: Item) => void;
}

const LABELS: Record<ItemState, string> = {
  needs_region: "Needs a region",
  ready: "Ready",
  queued: "Queued",
  processing: "Cleaning",
  completed: "Done",
  failed: "Failed",
  canceled: "Canceled",
};

function Thumb({ item }: { item: Item }) {
  const [broken, setBroken] = useState(false);
  if (broken) return <span className="thumb thumb-empty" aria-hidden="true" />;
  return (
    <img
      className="thumb"
      src={`/api/videos/${item.id}/frame?t=0&cleaned=0`}
      alt=""
      loading="lazy"
      onError={() => setBroken(true)}
    />
  );
}

export default function QueueRail({
  items,
  selectedId,
  onSelect,
  onRemove,
  onCancel,
  onRetry,
  onReveal,
}: Props) {
  return (
    <ul className="queue" aria-label="Video queue">
      {items.map((item) => {
        const active = item.state === "queued" || item.state === "processing";
        const showBar = active || item.state === "completed";
        return (
          <li key={item.id} className="queue-item" data-selected={item.id === selectedId}>
            <button
              type="button"
              className="queue-main"
              onClick={() => onSelect(item.id)}
              aria-current={item.id === selectedId ? "true" : undefined}
            >
              <Thumb item={item} />
              <span className="queue-text">
                <span className="queue-name" title={item.name}>
                  {item.name}
                </span>
                <span className="queue-meta">
                  {item.width}x{item.height}, {formatTime(item.duration_ms)}
                </span>
                <span className="queue-state" data-state={item.state}>
                  {LABELS[item.state]}
                  {item.state === "processing" ? ` ${item.progress}%` : ""}
                </span>
              </span>
            </button>
            {showBar && (
              <progress
                className="bar"
                max={100}
                value={item.state === "completed" ? 100 : item.progress}
                aria-label={`${item.name} progress`}
              />
            )}
            {item.error && <p className="queue-error">{item.error}</p>}
            <div className="queue-actions">
              {(item.state === "failed" || item.state === "canceled") && (
                <button type="button" className="link" onClick={() => onRetry(item)}>
                  Retry
                </button>
              )}
              {active && (
                <button type="button" className="link" onClick={() => onCancel(item)}>
                  Cancel
                </button>
              )}
              {item.state === "completed" && (
                <button type="button" className="link" onClick={() => onReveal(item)}>
                  Show in folder
                </button>
              )}
              {item.state !== "processing" && (
                <button
                  type="button"
                  className="link link-danger"
                  onClick={() => onRemove(item)}
                  aria-label={`Remove ${item.name}`}
                >
                  Remove
                </button>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
