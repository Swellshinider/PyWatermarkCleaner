"""Optimized preview rendering and latest-only background coordination."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from .cancellation import CancellationToken
from .exceptions import PreviewError
from .export import resolve_ffmpeg_executable
from .geometry import NormalizedRegion
from .inpainting import inpaint_frame, prepare_inpainting
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


def scale_to_long_edge(frame: NDArray[np.uint8], limit: int = 1280) -> NDArray[np.uint8]:
    """Downscale ``frame`` so its long edge is at most ``limit`` pixels."""
    height, width = frame.shape[:2]
    long_edge = max(width, height)
    if long_edge <= limit:
        return frame
    scale = limit / long_edge
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cast(NDArray[np.uint8], cv2.resize(frame, target, interpolation=cv2.INTER_AREA))


def render_preview_clip(
    path: Path,
    start_ms: int,
    seconds: float,
    region: NormalizedRegion,
    options: ProcessingOptions,
    out_path: Path,
    token: CancellationToken,
) -> Path:
    """Render a short cleaned H.264 clip (long edge <= 1280) with audio from the same offset."""
    path = Path(path)
    out_path = Path(out_path)
    if start_ms < 0 or seconds <= 0:
        raise PreviewError("Preview clip needs a nonnegative start and a positive duration.")
    capture = cv2.VideoCapture(str(path))
    process: subprocess.Popen[bytes] | None = None
    try:
        if not capture.isOpened():
            raise PreviewError(f"Could not open '{path}' for a preview clip.")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if not fps > 0:
            raise PreviewError(f"'{path}' reports an invalid frame rate.")
        capture.set(cv2.CAP_PROP_POS_MSEC, float(start_ms))
        # Sequential decode after a single seek; the first frame fixes the output size.
        ok, first = capture.read()
        if not ok or first is None:
            raise PreviewError(f"Could not decode '{path}' at {start_ms} ms.")
        frame = scale_to_long_edge(cast(NDArray[np.uint8], first))
        # libx264 with yuv420p needs even dimensions.
        width, height = frame.shape[1] // 2 * 2 or 2, frame.shape[0] // 2 * 2 or 2
        plan = prepare_inpainting((height, width, 3), region, options)
        command = [
            str(resolve_ffmpeg_executable()),
            "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
            "-r", f"{fps:.6f}", "-i", "-",
            "-ss", f"{start_ms / 1000:.3f}", "-t", f"{seconds:.3f}", "-i", str(path),
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
            str(out_path),
        ]  # fmt: skip
        out_path.parent.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL
        )
        assert process.stdin is not None
        frames = max(1, round(seconds * fps))
        current: NDArray[np.uint8] | None = frame
        for _ in range(frames):
            token.raise_if_cancelled()
            if current is None:
                break
            if current.shape[1] != width or current.shape[0] != height:
                current = cast(NDArray[np.uint8], cv2.resize(current, (width, height)))
            process.stdin.write(plan.apply(current).tobytes())
            ok, nxt = capture.read()
            current = scale_to_long_edge(cast(NDArray[np.uint8], nxt)) if ok else None
        process.stdin.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        if process.wait() != 0 or not out_path.is_file():
            raise PreviewError(f"FFmpeg could not render the preview clip: {stderr.strip()[-500:]}")
        return out_path
    except BaseException:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        out_path.unlink(missing_ok=True)
        raise
    finally:
        capture.release()
