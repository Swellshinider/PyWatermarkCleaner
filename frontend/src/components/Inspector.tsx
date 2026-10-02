import { useState } from "react";
import type { Item, Method, Performance, Settings } from "../api";
import { clampRect, fromRegion, type Rect } from "../regionEditor";

interface Props {
  item: Item | null;
  settings: Settings;
  maxWorkers: number;
  locked: boolean;
  onRegion: (item: Item, rect: Rect | null) => void;
  onApplyAll: (item: Item) => void;
  onSettings: (patch: Partial<Settings>) => void;
  onPickFolder: () => void;
}

const FIELDS: { key: keyof Rect; label: string }[] = [
  { key: "x", label: "X" },
  { key: "y", label: "Y" },
  { key: "w", label: "Width" },
  { key: "h", label: "Height" },
];

const PERFORMANCE: { value: Performance; label: string }[] = [
  { value: "fast", label: "Fast" },
  { value: "balanced", label: "Balanced" },
  { value: "quality", label: "Quality" },
];

export default function Inspector({
  item,
  settings,
  maxWorkers,
  locked,
  onRegion,
  onApplyAll,
  onSettings,
  onPickFolder,
}: Props) {
  const [advanced, setAdvanced] = useState(false);
  const rect = item?.region ? fromRegion(item.region, { w: item.width, h: item.height }) : null;

  const setField = (key: keyof Rect, raw: string) => {
    if (!item || !rect) return;
    const v = Number(raw);
    if (!Number.isFinite(v)) return;
    const next = clampRect({ ...rect, [key]: v }, { w: item.width, h: item.height });
    if (next[key] !== rect[key] || next.x !== rect.x || next.y !== rect.y) onRegion(item, next);
  };

  return (
    <div className="inspector">
      <fieldset disabled={locked} className="group">
        <legend>Region</legend>
        <p className="group-note">
          {rect
            ? `In source pixels. Frame is ${item!.width} by ${item!.height}.`
            : "Draw on the video to set the watermark area."}
        </p>
        <div className="grid-fields">
          {FIELDS.map(({ key, label }) => (
            <label key={key} className="field">
              <span>{label}</span>
              <input
                key={`${key}-${rect ? rect[key] : ""}`}
                className="mono"
                type="number"
                inputMode="numeric"
                min={0}
                disabled={!rect}
                defaultValue={rect ? rect[key] : ""}
                onBlur={(e) => setField(key, e.currentTarget.value)}
                onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
              />
            </label>
          ))}
        </div>
        <div className="row">
          <button
            type="button"
            className="btn"
            disabled={!rect}
            onClick={() => item && onApplyAll(item)}
          >
            Apply to all videos
          </button>
          <button
            type="button"
            className="link"
            disabled={!rect}
            onClick={() => item && onRegion(item, null)}
          >
            Clear
          </button>
        </div>
      </fieldset>

      <fieldset disabled={locked} className="group">
        <legend>Cleaning</legend>
        <label className="field">
          <span>Method</span>
          <select
            value={settings.method}
            onChange={(e) => onSettings({ method: e.target.value as Method })}
          >
            <option value="telea">Telea</option>
            <option value="navier-stokes">Navier-Stokes</option>
          </select>
        </label>
        <label className="field">
          <span>
            Radius <output className="mono">{settings.radius}</output>
          </span>
          <input
            type="range"
            min={1}
            max={10}
            value={settings.radius}
            onChange={(e) => onSettings({ radius: Number(e.target.value) })}
          />
        </label>
        <div className="field">
          <span id="perf-label">Performance</span>
          <div className="segmented" role="radiogroup" aria-labelledby="perf-label">
            {PERFORMANCE.map((p) => (
              <button
                key={p.value}
                type="button"
                role="radio"
                aria-checked={settings.performance === p.value}
                onClick={() => onSettings({ performance: p.value })}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </fieldset>

      <fieldset disabled={locked} className="group">
        <legend>Output</legend>
        <div className="folder">
          <code className="mono folder-path" title={settings.output_folder}>
            {settings.output_folder || "Default folder"}
          </code>
          <button type="button" className="btn" onClick={onPickFolder}>
            Choose folder
          </button>
        </div>
      </fieldset>

      <div className="group">
        <button
          type="button"
          className="disclosure"
          aria-expanded={advanced}
          onClick={() => setAdvanced((a) => !a)}
        >
          Advanced
        </button>
        {advanced && (
          <fieldset disabled={locked} className="plain">
            <label className="field">
              <span>Workers (videos at once)</span>
              <input
                className="mono"
                type="number"
                min={1}
                max={maxWorkers}
                value={settings.workers}
                onChange={(e) => {
                  const v = Math.round(Number(e.target.value));
                  if (v >= 1 && v <= maxWorkers) onSettings({ workers: v });
                }}
              />
            </label>
            <p className="group-note">Up to {maxWorkers} on this computer.</p>
          </fieldset>
        )}
      </div>
    </div>
  );
}
