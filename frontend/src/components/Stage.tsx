import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, frameUrl, proxyUrl, sourceUrl, type Item, type Settings } from "../api";
import {
  HANDLES,
  beginDrag,
  fromRegion,
  hitTest,
  letterbox,
  nudge,
  screenToSource,
  updateDrag,
  type Drag,
  type Handle,
  type Rect,
} from "../regionEditor";
import { formatTime } from "../state";

interface Props {
  item: Item;
  settings: Settings;
  onRegion: (item: Item, rect: Rect | null) => Promise<void>;
  onStatus: (message: string) => void;
}

const CURSORS: Record<Handle | "move", string> = {
  nw: "nwse-resize",
  se: "nwse-resize",
  ne: "nesw-resize",
  sw: "nesw-resize",
  n: "ns-resize",
  s: "ns-resize",
  e: "ew-resize",
  w: "ew-resize",
  move: "move",
};

const isFormControl = (t: EventTarget | null) =>
  t instanceof HTMLElement && /^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(t.tagName);

export default function Stage({ item, settings, onRegion, onStatus }: Props) {
  const source = { w: item.width, h: item.height };
  const viewportRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [useProxy, setUseProxy] = useState(false);
  const [mediaError, setMediaError] = useState(false);
  const [viewCleaned, setViewCleaned] = useState(true);
  const [holdOriginal, setHoldOriginal] = useState(false);
  const [draft, setDraft] = useState<Rect | null>(null);
  const [dragging, setDragging] = useState(false);
  const [hover, setHover] = useState<Handle | "move" | null>(null);
  const [frame, setFrame] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ url: string; sig: string } | null>(null);
  const [rendering, setRendering] = useState(false);

  const dragRef = useRef<{ drag: Drag; restore: Rect | null; moved: boolean } | null>(null);
  const draftRef = useRef<Rect | null>(null);
  const nudgeTimer = useRef<number | undefined>(undefined);
  const resumeAt = useRef<number | null>(null);
  const frameRef = useRef<string | null>(null);

  const committed = item.region ? fromRegion(item.region, source) : null;
  const shown = draft ?? committed;
  const lb = letterbox(box, source);
  const sig = `${JSON.stringify(item.region)}|${settings.method}|${settings.radius}|${settings.performance}`;
  const previewUrl = preview && preview.sig === sig ? preview.url : null;
  const showingCleaned = viewCleaned && !holdOriginal && !!item.region;

  const updateDraft = useCallback((r: Rect | null) => {
    draftRef.current = r;
    setDraft(r);
  }, [setDraft]);

  useLayoutEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const read = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Hold Space to peek at the original.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !isFormControl(e.target)) {
        e.preventDefault();
        setHoldOriginal(true);
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space") setHoldOriginal(false);
    };
    const blur = () => setHoldOriginal(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, []);

  // Smooth timecode while playing.
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      if (videoRef.current) setTime(videoRef.current.currentTime * 1000);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  // Cleaned still for the paused frame: debounced, latest wins, stale fetches aborted.
  const wantFrame = !playing && !previewUrl && showingCleaned && !dragging && !draft && !mediaError;
  const tRound = Math.round(time);
  useEffect(() => {
    if (!wantFrame) return;
    const ctl = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const res = await fetch(frameUrl(item.id, tRound, true), {
          signal: ctl.signal,
          credentials: "same-origin",
        });
        if (!res.ok) return;
        const url = URL.createObjectURL(await res.blob());
        if (ctl.signal.aborted) {
          URL.revokeObjectURL(url);
          return;
        }
        if (frameRef.current) URL.revokeObjectURL(frameRef.current);
        frameRef.current = url;
        setFrame(url);
      } catch {
        /* aborted or offline: keep the previous still */
      }
    }, 120);
    return () => {
      window.clearTimeout(timer);
      ctl.abort();
    };
  }, [wantFrame, item.id, tRound, sig]);

  useEffect(
    () => () => {
      if (frameRef.current) URL.revokeObjectURL(frameRef.current);
      window.clearTimeout(nudgeTimer.current);
    },
    [],
  );

  const commit = useCallback(
    async (rect: Rect | null) => {
      try {
        await onRegion(item, rect);
      } finally {
        updateDraft(null);
      }
    },
    [item, onRegion, updateDraft],
  );

  const pointSource = (e: React.PointerEvent) => {
    const r = viewportRef.current!.getBoundingClientRect();
    return screenToSource({ x: e.clientX - r.left, y: e.clientY - r.top }, lb, source);
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0 || !lb.w) return;
    window.clearTimeout(nudgeTimer.current);
    const p = pointSource(e);
    const hit = hitTest(shown, p, 9 / lb.scale);
    const drag = hit && shown ? beginDrag(hit, p, shown) : beginDrag("draw", p, null);
    dragRef.current = { drag, restore: shown, moved: false };
    overlayRef.current?.setPointerCapture(e.pointerId);
    setDragging(true);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const p = pointSource(e);
    const d = dragRef.current;
    if (!d) {
      const hit = hitTest(shown, p, 9 / lb.scale);
      if (hit !== hover) setHover(hit);
      return;
    }
    const dx = (p.x - d.drag.start.x) * lb.scale;
    const dy = (p.y - d.drag.start.y) * lb.scale;
    if (!d.moved && Math.hypot(dx, dy) < 3) return;
    d.moved = true;
    updateDraft(updateDrag(d.drag, p, source));
  };

  const endDrag = (e: React.PointerEvent, cancel: boolean) => {
    const d = dragRef.current;
    if (!d) return;
    dragRef.current = null;
    setDragging(false);
    overlayRef.current?.releasePointerCapture(e.pointerId);
    const rect = draftRef.current;
    if (cancel || !d.moved || !rect) {
      updateDraft(null);
      return;
    }
    void commit(rect);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape" && dragRef.current) {
      dragRef.current = null;
      setDragging(false);
      updateDraft(null);
      e.preventDefault();
      return;
    }
    if (!shown) return;
    const next = nudge(shown, e.key, e.shiftKey, source);
    if (!next) return;
    e.preventDefault();
    updateDraft(next);
    window.clearTimeout(nudgeTimer.current);
    nudgeTimer.current = window.setTimeout(() => {
      if (draftRef.current) void commit(draftRef.current);
    }, 250);
  };

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) void v.play().catch(() => undefined);
    else v.pause();
  };

  const seek = (ms: number) => {
    if (videoRef.current) videoRef.current.currentTime = ms / 1000;
    setTime(ms);
  };

  const renderPreview = async () => {
    setRendering(true);
    onStatus("Rendering a short cleaned preview.");
    try {
      const { url } = await api.previewClip(item.id, time);
      videoRef.current?.pause();
      setPreview({ url, sig });
      onStatus("Preview ready.");
    } catch (err) {
      onStatus(err instanceof Error ? err.message : "Could not render the preview.");
    } finally {
      setRendering(false);
    }
  };

  const leavePreview = () => {
    resumeAt.current = time;
    setPreview(null);
  };

  const onVideoError = () => {
    if (previewUrl) {
      setPreview(null);
      onStatus("The preview clip could not be played.");
    } else if (!item.needs_proxy && !useProxy) {
      resumeAt.current = videoRef.current?.currentTime ?? 0;
      setUseProxy(true);
    } else {
      setMediaError(true);
    }
  };

  const src = previewUrl ?? (item.needs_proxy || useProxy ? proxyUrl(item.id) : sourceUrl(item.id));
  const pct = (v: number, total: number) => `${(v / total) * 100}%`;
  const clip = shown
    ? `inset(${pct(shown.y, item.height)} ${pct(item.width - shown.x - shown.w, item.width)} ${pct(item.height - shown.y - shown.h, item.height)} ${pct(shown.x, item.width)})`
    : undefined;

  return (
    <section className="stage" aria-label="Video stage">
      <div className="viewport" ref={viewportRef}>
        <div
          className="frame"
          style={{ left: lb.x, top: lb.y, width: lb.w, height: lb.h }}
          data-playing={playing}
        >
          <video
            ref={videoRef}
            src={src}
            className="video"
            playsInline
            preload="metadata"
            autoPlay={!!previewUrl}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onTimeUpdate={(e) => !playing && setTime(e.currentTarget.currentTime * 1000)}
            onSeeked={(e) => setTime(e.currentTarget.currentTime * 1000)}
            onLoadedMetadata={(e) => {
              if (resumeAt.current !== null) {
                e.currentTarget.currentTime = resumeAt.current / 1000;
                resumeAt.current = null;
              }
            }}
            onError={onVideoError}
          />
          {frame && wantFrame && clip && (
            <img className="cleaned" src={frame} alt="" style={{ clipPath: clip }} />
          )}
          <div
            ref={overlayRef}
            className="overlay"
            tabIndex={0}
            role="application"
            aria-label="Region editor. Drag to mark the watermark, drag the region to move it, use arrow keys to nudge by one pixel, Shift and arrow keys for ten."
            style={{ cursor: hover ? CURSORS[hover] : "crosshair" }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={(e) => endDrag(e, false)}
            onPointerCancel={(e) => endDrag(e, true)}
            onKeyDown={onKeyDown}
          >
            {shown && !previewUrl && (
              <div
                className="region"
                data-dragging={dragging}
                style={{
                  left: pct(shown.x, item.width),
                  top: pct(shown.y, item.height),
                  width: pct(shown.w, item.width),
                  height: pct(shown.h, item.height),
                }}
              >
                {HANDLES.map((h) => (
                  <span key={h} className={`handle handle-${h}`} />
                ))}
              </div>
            )}
          </div>
          {previewUrl && <span className="badge badge-preview">Preview clip</span>}
          {!previewUrl && item.region && (
            <span className="badge">{showingCleaned && !playing ? "Cleaned" : "Original"}</span>
          )}
        </div>
        {!item.region && !mediaError && (
          <p className="stage-hint">Drag on the video to mark the watermark.</p>
        )}
        {mediaError && (
          <p className="stage-hint stage-error" role="alert">
            This video can&apos;t be played in the browser. You can still set a region from the numbers
            on the right, and clean it.
          </p>
        )}
      </div>

      <div className="transport">
        <button
          type="button"
          className="icon-btn"
          onClick={togglePlay}
          aria-label={playing ? "Pause" : "Play"}
        >
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
            {playing ? (
              <path d="M7 5h4v14H7zM13 5h4v14h-4z" fill="currentColor" />
            ) : (
              <path d="M8 5v14l11-7z" fill="currentColor" />
            )}
          </svg>
        </button>
        <input
          className="scrubber"
          type="range"
          min={0}
          max={Math.max(item.duration_ms, 1)}
          step={1}
          value={Math.min(time, Math.max(item.duration_ms, 1))}
          onChange={(e) => seek(Number(e.target.value))}
          aria-label="Position"
          aria-valuetext={formatTime(time)}
        />
        <span className="timecode" aria-hidden="true">
          {formatTime(time)} / {formatTime(item.duration_ms)}
        </span>
      </div>

      <div className="stage-tools">
        <div className="segmented" role="group" aria-label="Paused view">
          <button type="button" aria-pressed={viewCleaned} onClick={() => setViewCleaned(true)}>
            Cleaned
          </button>
          <button type="button" aria-pressed={!viewCleaned} onClick={() => setViewCleaned(false)}>
            Original
          </button>
        </div>
        <span className="hint">Hold Space to see the original.</span>
        <span className="grow" />
        {previewUrl ? (
          <button type="button" className="btn" onClick={leavePreview}>
            Back to full video
          </button>
        ) : (
          <button
            type="button"
            className="btn"
            onClick={renderPreview}
            disabled={!item.region || rendering || mediaError}
          >
            {rendering ? "Rendering..." : "Preview clip"}
          </button>
        )}
      </div>
      <span className="sr-only" aria-live="polite">
        {holdOriginal ? "Showing original" : ""}
      </span>
    </section>
  );
}
