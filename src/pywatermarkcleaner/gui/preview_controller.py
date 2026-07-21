"""Qt signal adapter for latest-only silent preview work."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import cv2
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot

from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.media import OpenCVMediaReader
from pywatermarkcleaner.core.models import (
    InpaintMethod,
    PreviewRequest,
    PreviewResult,
    ProcessingOptions,
)
from pywatermarkcleaner.core.preview import LatestPreviewCoordinator, PreviewService

from .queue import QueueItem


class _PreviewBridge(QObject):
    result = Signal(object)
    error = Signal(object)


class _SourceSignals(QObject):
    result = Signal(int, object, object, int)
    error = Signal(int, object)
    finished = Signal(object)


class _SourceTask(QRunnable):
    def __init__(self, reader: object, path: Path, timestamp_ms: int, generation: int) -> None:
        super().__init__()
        self.reader = reader
        self.path = path
        self.timestamp_ms = timestamp_ms
        self.generation = generation
        self.signals = _SourceSignals()

    @Slot()
    def run(self) -> None:
        try:
            frame = self.reader.read_frame(self.path, self.timestamp_ms)  # type: ignore[attr-defined]
            height, width = frame.shape[:2]
            if max(width, height) > 1280:
                scale = 1280 / max(width, height)
                frame = cv2.resize(
                    frame,
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
        except Exception as error:
            self.signals.error.emit(self.generation, error)
        else:
            self.signals.result.emit(self.generation, self.path, frame, self.timestamp_ms)
        finally:
            self.signals.finished.emit(self)


class PreviewController(QObject):
    """Own preview sequencing, stale suppression, and source-time playback."""

    frame_ready = Signal(object, object, object, int)
    failed = Signal(str)
    timestamp_changed = Signal(int)
    request_submitted = Signal(object)

    def __init__(
        self,
        *,
        coordinator_factory: Callable[[Callable[..., None], Callable[..., None]], object]
        | None = None,
        reader: object | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = _PreviewBridge(self)
        self._bridge.result.connect(self._accept_result, Qt.ConnectionType.QueuedConnection)
        self._bridge.error.connect(self._accept_error, Qt.ConnectionType.QueuedConnection)
        factory = coordinator_factory or self._default_factory
        self._coordinator = factory(self._bridge.result.emit, self._bridge.error.emit)
        self._reader = reader or OpenCVMediaReader()
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._source_generation = 0
        self._selection_generation = 0
        self._source_tasks: set[_SourceTask] = set()
        self._path: Path | None = None
        self._region: NormalizedRegion | None = None
        self._options = ProcessingOptions()
        self._sequence = 0
        self._latest_sequence = 0
        self._request_owners: dict[int, tuple[int, Path]] = {}
        self.timestamp_ms = 0
        self._duration_ms = 0
        self._frame_step_ms = 33
        self.audio_enabled = False
        self._closed = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._playback_tick)

    @staticmethod
    def _default_factory(on_result: Callable[..., None], on_error: Callable[..., None]) -> object:
        return LatestPreviewCoordinator(PreviewService(), on_result, on_error)

    def select_item(self, item: QueueItem) -> None:
        self._invalidate_selection()
        self._path = item.metadata.path
        self._region = item.region
        self.timestamp_ms = item.timeline_position_ms
        self._duration_ms = max(0, round(item.metadata.duration_seconds * 1000))
        self._frame_step_ms = max(1, round(1000 / item.metadata.fps))
        self.timestamp_changed.emit(self.timestamp_ms)
        if self._region is None:
            self._load_source()
        else:
            self._submit()

    @property
    def selected_path(self) -> Path | None:
        return self._path

    def _invalidate_selection(self) -> None:
        self.pause()
        self._selection_generation += 1
        self._source_generation += 1
        self._sequence += 1
        self._latest_sequence = self._sequence
        self._request_owners.clear()

    def clear_selection(self) -> None:
        self._invalidate_selection()
        self._path = None
        self._region = None
        self.timestamp_ms = 0
        self._duration_ms = 0
        self.timestamp_changed.emit(0)

    def _load_source(self) -> None:
        if self._path is None or self._closed:
            return
        self._source_generation += 1
        task = _SourceTask(self._reader, self._path, self.timestamp_ms, self._source_generation)
        task.signals.result.connect(self._source_result, Qt.ConnectionType.QueuedConnection)
        task.signals.error.connect(self._source_error, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._source_finished, Qt.ConnectionType.QueuedConnection)
        self._source_tasks.add(task)
        self._thread_pool.start(task)

    @Slot(int, object, object, int)
    def _source_result(self, generation: int, path: Path, frame: object, timestamp_ms: int) -> None:
        if not self._closed and generation == self._source_generation and path == self._path:
            self.frame_ready.emit(path, frame, None, timestamp_ms)

    @Slot(int, object)
    def _source_error(self, generation: int, error: Exception) -> None:
        if not self._closed and generation == self._source_generation:
            self.failed.emit(str(error))

    @Slot(object)
    def _source_finished(self, task: _SourceTask) -> None:
        self._source_tasks.discard(task)

    def set_region(self, region: NormalizedRegion) -> None:
        self._region = region
        self._submit()

    def set_timestamp(self, timestamp_ms: int) -> None:
        self.timestamp_ms = min(max(0, int(timestamp_ms)), self._duration_ms)
        self.timestamp_changed.emit(self.timestamp_ms)
        if self._region is None:
            self._load_source()
        else:
            self._submit()

    def set_method(self, method: str | InpaintMethod) -> None:
        selected = method if isinstance(method, InpaintMethod) else InpaintMethod(method)
        self._options = ProcessingOptions(selected, self._options.radius)
        self._submit()

    def set_radius(self, radius: int) -> None:
        self._options = ProcessingOptions(self._options.method, radius)
        self._submit()

    def _submit(self) -> None:
        if self._closed or self._path is None or self._region is None:
            return
        self._sequence += 1
        request = PreviewRequest(
            self._path,
            self.timestamp_ms,
            self._region,
            self._options,
            self._sequence,
        )
        self._latest_sequence = request.sequence
        self._request_owners = {request.sequence: (self._selection_generation, self._path)}
        self.request_submitted.emit(request)
        self._coordinator.submit(request)  # type: ignore[attr-defined]

    @Slot(object)
    def _accept_result(self, result: PreviewResult) -> None:
        owner = self._request_owners.pop(result.sequence, None)
        if (
            self._closed
            or result.sequence != self._latest_sequence
            or owner != (self._selection_generation, self._path)
            or self._path is None
        ):
            return
        self.frame_ready.emit(
            self._path,
            result.source_frame,
            result.cleaned_frame,
            result.timestamp_ms,
        )

    @Slot(object)
    def _accept_error(self, error: Exception) -> None:
        if not self._closed:
            self.failed.emit(str(error))

    def play(self) -> None:
        if self._path is not None and not self._timer.isActive():
            self._timer.start(self._frame_step_ms)

    def pause(self) -> None:
        self._timer.stop()

    def is_playing(self) -> bool:
        return self._timer.isActive()

    @Slot()
    def _playback_tick(self) -> None:
        next_timestamp = self.timestamp_ms + self._frame_step_ms
        if self._duration_ms and next_timestamp > self._duration_ms:
            self.pause()
            next_timestamp = self._duration_ms
        self.set_timestamp(next_timestamp)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._invalidate_selection()
        self._coordinator.close(wait=False)  # type: ignore[attr-defined]
