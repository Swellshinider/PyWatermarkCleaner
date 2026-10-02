"""In-memory queue state, event fan-out and cached frame access for one app session."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from pywatermarkcleaner import __version__
from pywatermarkcleaner.core.exceptions import MediaError
from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.models import JobState, VideoMetadata
from pywatermarkcleaner.core.scheduler import max_allowed_workers

from .system import Settings

# Containers browsers cannot decode natively; the frontend falls back to /proxy.
PROXY_CONTAINERS = frozenset({"mkv", "avi", "wmv", "flv"})
_ACTIVE = {JobState.QUEUED, JobState.PROCESSING}
_SEQUENTIAL_WINDOW = 30  # frames skipped by decoding instead of seeking


@dataclass
class Item:
    """One queued video; mirrors the per-row state machine of the retired desktop queue."""

    metadata: VideoMetadata
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    region: NormalizedRegion | None = None
    state: JobState = JobState.NEEDS_REGION
    progress: int = 0
    output_path: Path | None = None
    error: str = ""
    uploaded: bool = False
    clip_version: int = 0
    # Serializes proxy builds per item.
    proxy_lock: RLock = field(default_factory=RLock)

    @property
    def path(self) -> Path:
        return self.metadata.path

    def to_dict(self) -> dict[str, Any]:
        meta = self.metadata
        region = self.region
        return {
            "id": self.id,
            "name": meta.path.name,
            "width": meta.width,
            "height": meta.height,
            "fps": meta.fps,
            "duration_ms": round(meta.duration_seconds * 1000),
            "container": meta.container,
            "state": self.state.value.replace("-", "_"),
            "progress": self.progress,
            "region": None
            if region is None
            else {"x": region.x, "y": region.y, "width": region.width, "height": region.height},
            "output_path": None if self.output_path is None else str(self.output_path),
            "error": self.error,
            "needs_proxy": meta.container in PROXY_CONTAINERS,
        }


def valid_region(metadata: VideoMetadata, region: NormalizedRegion | None) -> bool:
    """A region is usable only when it covers at least 2x2 source pixels."""
    if region is None:
        return False
    try:
        pixels = region.to_pixels(metadata.width, metadata.height)
    except ValueError:
        return False
    return pixels.width >= 2 and pixels.height >= 2


class FrameCache:
    """One lock-protected cv2.VideoCapture per video, reused across frame requests."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        self._capture: cv2.VideoCapture | None = None
        self._next = 0  # index of the frame the capture will return next
        self._last: tuple[int, NDArray[np.uint8]] | None = None

    def read_frame(self, path: object, timestamp_ms: int) -> NDArray[np.uint8]:
        """Satisfy the preview ``FrameReader`` protocol (``path`` is fixed per cache)."""
        with self._lock:
            if self._capture is None:
                if not self._path.is_file():
                    raise MediaError(f"Media file does not exist: {self._path}")
                self._capture = cv2.VideoCapture(str(self._path))
                if not self._capture.isOpened():
                    self.close()
                    raise MediaError(f"Could not open media '{self._path}'.")
                self._next = 0
                self._last = None
            capture = self._capture
            fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
            target = max(0, round(timestamp_ms / 1000 * fps))
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if count > 0:
                target = min(target, count - 1)
            if self._last is not None and self._last[0] == target:
                return self._last[1].copy()
            if 0 <= target - self._next <= _SEQUENTIAL_WINDOW:
                for _ in range(target - self._next):
                    capture.grab()
            else:
                capture.set(cv2.CAP_PROP_POS_FRAMES, float(target))
            ok, frame = capture.read()
            if not ok or not isinstance(frame, np.ndarray) or frame.ndim != 3:
                self._last = None
                self._next = -1_000_000  # force a seek next time
                raise MediaError(f"Could not decode a frame at {timestamp_ms} ms.")
            self._next = target + 1
            self._last = (target, cast(NDArray[np.uint8], frame))
            return self._last[1].copy()

    def close(self) -> None:
        with self._lock:
            if self._capture is not None:
                self._capture.release()
            self._capture = None
            self._last = None


class Session:
    """All mutable application state, guarded by one re-entrant lock."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = RLock()
        self.items: list[Item] = []
        self.running = False
        self._frames: dict[str, FrameCache] = {}
        self._workspace: Path | None = None
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[str]]] = set()

    # -- items -------------------------------------------------------------
    def get(self, item_id: str) -> Item | None:
        with self.lock:
            return next((item for item in self.items if item.id == item_id), None)

    def add(self, metadata: VideoMetadata, *, uploaded: bool = False) -> Item | None:
        """Add a probed video; ``None`` when the same file is already queued."""
        canonical = Path(metadata.path).resolve()
        with self.lock:
            if not uploaded and any(item.path == canonical for item in self.items):
                return None
            stored = VideoMetadata(
                canonical,
                metadata.width,
                metadata.height,
                metadata.fps,
                metadata.frame_count,
                metadata.duration_seconds,
                metadata.container,
            )
            item = Item(stored, uploaded=uploaded)
            self.items.append(item)
            return item

    def remove(self, item_id: str) -> Item | None:
        with self.lock:
            item = self.get(item_id)
            if item is None:
                return None
            self.items.remove(item)
            cache = self._frames.pop(item_id, None)
        if cache is not None:
            cache.close()
        if self._workspace is not None:
            for leftover in self._workspace.glob(f"{item_id}-*"):
                leftover.unlink(missing_ok=True)
        if item.uploaded:
            shutil.rmtree(item.path.parent, ignore_errors=True)
        return item

    def set_region(self, item: Item, region: NormalizedRegion | None) -> bool:
        with self.lock:
            active = item.state in _ACTIVE
            valid = valid_region(item.metadata, region)
            item.region = region if valid else None
            if not active:
                item.state = JobState.READY if valid else JobState.NEEDS_REGION
            return valid

    def all_ready(self) -> bool:
        with self.lock:
            return bool(self.items) and all(
                valid_region(item.metadata, item.region) for item in self.items
            )

    def batch_progress(self) -> int:
        with self.lock:
            if not self.items:
                return 0
            return sum(item.progress for item in self.items) // len(self.items)

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {
                "items": [item.to_dict() for item in self.items],
                "settings": {
                    "method": self.settings.method,
                    "radius": self.settings.radius,
                    "performance": self.settings.performance,
                    "workers": self.settings.workers,
                    "output_folder": self.settings.output_folder,
                    "format_policy": self.settings.format_policy,
                },
                "max_workers": max_allowed_workers(),
                "running": self.running,
                "batch_progress": self.batch_progress(),
                "version": __version__,
            }

    # -- files -------------------------------------------------------------
    @property
    def workspace(self) -> Path:
        """Temp directory for uploads, proxies and preview clips (removed on close)."""
        with self.lock:
            if self._workspace is None:
                self._workspace = Path(tempfile.mkdtemp(prefix="pywatermarkcleaner-"))
            return self._workspace

    def frames(self, item: Item) -> FrameCache:
        with self.lock:
            cache = self._frames.get(item.id)
            if cache is None:
                cache = self._frames[item.id] = FrameCache(item.path)
            return cache

    def close(self) -> None:
        with self.lock:
            caches = list(self._frames.values())
            self._frames.clear()
            workspace, self._workspace = self._workspace, None
        for cache in caches:
            cache.close()
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)

    # -- events ------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue[str]:
        """Register the running loop's SSE client; call from the event loop."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        with self.lock:
            self._subscribers.add((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        with self.lock:
            self._subscribers = {pair for pair in self._subscribers if pair[1] is not queue}

    @property
    def client_count(self) -> int:
        with self.lock:
            return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        """Queue an event for every client; safe to call from any thread."""
        message = json.dumps(event)
        with self.lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, message)
            except RuntimeError:  # loop already closed
                self.unsubscribe(queue)

    def publish_state(self) -> None:
        self.publish({"type": "state", "state": self.state()})

    def publish_item(self, item: Item) -> None:
        self.publish({"type": "item", "item": item.to_dict()})

    def log(self, message: str) -> None:
        self.publish({"type": "log", "message": message})
