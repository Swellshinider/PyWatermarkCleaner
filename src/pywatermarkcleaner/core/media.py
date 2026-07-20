"""OpenCV-backed, Qt-free metadata and frame access."""

from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from pathlib import Path
from typing import Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from .exceptions import MediaError
from .models import VideoMetadata


class VideoCapture(Protocol):
    def isOpened(self) -> bool: ...

    def get(self, property_id: int) -> float: ...

    def set(self, property_id: int, value: float) -> bool: ...

    def read(self) -> tuple[bool, NDArray[np.uint8] | None]: ...

    def release(self) -> None: ...


CaptureFactory = Callable[[str], VideoCapture]


class OpenCVMediaReader:
    """Read media facts and individual BGR frames through OpenCV."""

    def __init__(self, capture_factory: CaptureFactory | None = None) -> None:
        self._capture_factory = capture_factory or cast(CaptureFactory, cv2.VideoCapture)

    def _open(self, path: Path) -> VideoCapture:
        if not path.is_file():
            raise MediaError(f"Media file does not exist: {path}")
        capture = self._capture_factory(str(path))
        if not capture.isOpened():
            capture.release()
            raise MediaError(
                f"Could not open media '{path}'. Check that the file is readable and uses a "
                "codec supported by this OpenCV installation."
            )
        return capture

    def probe(self, path: Path) -> VideoMetadata:
        """Validate and return the media properties needed by the application."""
        path = Path(path)
        capture = self._open(path)
        try:
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            frame_count = max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
            if width <= 0 or height <= 0 or not isfinite(fps) or fps <= 0:
                raise MediaError(
                    f"Media '{path}' has invalid video metadata. Try remuxing or transcoding it."
                )
            return VideoMetadata(
                path=path,
                width=width,
                height=height,
                fps=fps,
                frame_count=frame_count,
                duration_seconds=frame_count / fps,
                container=path.suffix.lower().lstrip("."),
            )
        finally:
            capture.release()

    def read_frame(self, path: Path, timestamp_ms: int) -> NDArray[np.uint8]:
        """Seek to ``timestamp_ms`` and decode one BGR uint8 frame."""
        path = Path(path)
        if timestamp_ms < 0:
            raise MediaError(f"Cannot seek '{path}' to a negative timestamp: {timestamp_ms} ms")
        capture = self._open(path)
        try:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(timestamp_ms))
            succeeded, frame = capture.read()
            if (
                not succeeded
                or not isinstance(frame, np.ndarray)
                or frame.dtype != np.uint8
                or frame.ndim != 3
                or frame.shape[2] != 3
                or frame.size == 0
            ):
                raise MediaError(
                    f"Could not decode a BGR frame from '{path}' at {timestamp_ms} ms. "
                    "Try a different timestamp or transcode the source."
                )
            return frame
        finally:
            capture.release()
