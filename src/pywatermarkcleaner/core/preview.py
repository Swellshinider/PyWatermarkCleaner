"""Optimized preview rendering and latest-only background coordination."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import RLock
from typing import Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from .exceptions import PreviewError
from .geometry import NormalizedRegion
from .inpainting import inpaint_frame
from .media import OpenCVMediaReader
from .models import PreviewRequest, PreviewResult, ProcessingOptions


class FrameReader(Protocol):
    def read_frame(self, path: object, timestamp_ms: int) -> NDArray[np.uint8]: ...


InpaintOperation = Callable[
    [NDArray[np.uint8], NormalizedRegion, ProcessingOptions], NDArray[np.uint8]
]


class PreviewRenderer(Protocol):
    def render(self, request: PreviewRequest) -> PreviewResult: ...


class PreviewService:
    """Decode and clean one preview frame, capped at a 1280-pixel long edge."""

    def __init__(
        self,
        reader: FrameReader | None = None,
        *,
        inpaint_operation: InpaintOperation = inpaint_frame,
    ) -> None:
        self._reader = reader or OpenCVMediaReader()
        self._inpaint = inpaint_operation

    def render(self, request: PreviewRequest) -> PreviewResult:
        try:
            source = self._reader.read_frame(request.path, request.timestamp_ms)
            height, width = source.shape[:2]
            long_edge = max(width, height)
            if long_edge > 1280:
                scale = 1280 / long_edge
                target = (max(1, round(width * scale)), max(1, round(height * scale)))
                source = cast(
                    NDArray[np.uint8],
                    cv2.resize(source, target, interpolation=cv2.INTER_AREA),
                )
            cleaned = self._inpaint(source, request.region, request.options)
        except Exception as error:
            if isinstance(error, PreviewError):
                raise
            raise PreviewError(
                f"Could not render preview for '{request.path}' at "
                f"{request.timestamp_ms} ms: {error}"
            ) from error
        return PreviewResult(
            request.sequence,
            request.timestamp_ms,
            source,
            cleaned,
        )


class LatestPreviewCoordinator:
    """Serialize preview work while retaining only the newest pending request."""

    def __init__(
        self,
        service: PreviewRenderer,
        on_result: Callable[[PreviewResult], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self._service = service
        self._on_result = on_result
        self._on_error = on_error
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview")
        self._lock = RLock()
        self._closed = False
        self._generation = 0
        self._active = False
        self._pending: tuple[int, PreviewRequest] | None = None

    def submit(self, request: PreviewRequest) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("preview coordinator is closed")
            self._generation += 1
            pending = (self._generation, request)
            if self._active:
                self._pending = pending
            else:
                self._start_locked(*pending)

    def _start_locked(self, generation: int, request: PreviewRequest) -> None:
        self._active = True
        future = self._executor.submit(self._service.render, request)

        def completed_callback(completed: Future[PreviewResult]) -> None:
            self._completed(generation, completed)

        future.add_done_callback(completed_callback)

    def _completed(self, generation: int, future: Future[PreviewResult]) -> None:
        with self._lock:
            if self._closed:
                self._active = False
                self._pending = None
                return
            is_latest = generation == self._generation
            if is_latest:
                try:
                    self._on_result(future.result())
                except Exception as error:
                    self._on_error(error)
            if self._pending is not None:
                next_generation, next_request = self._pending
                self._pending = None
                self._start_locked(next_generation, next_request)
            else:
                self._active = False

    def close(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._pending = None
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def __enter__(self) -> LatestPreviewCoordinator:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
