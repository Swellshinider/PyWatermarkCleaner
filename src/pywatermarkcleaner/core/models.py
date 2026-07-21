"""Immutable domain models and worker message contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from .geometry import NormalizedRegion


class InpaintMethod(StrEnum):
    """OpenCV inpainting algorithms exposed by the application."""

    TELEA = "telea"
    NAVIER_STOKES = "navier-stokes"


class PerformanceMode(StrEnum):
    """Speed/quality trade-off used by video encoders."""

    FAST = "fast"
    BALANCED = "balanced"
    QUALITY = "quality"


class FormatPolicy(StrEnum):
    """Whether a batch keeps source containers or is converted to MP4."""

    ORIGINAL = "original"
    MP4 = "mp4"


@dataclass(frozen=True)
class ProcessingOptions:
    """User-selectable inpainting settings."""

    method: InpaintMethod = InpaintMethod.TELEA
    radius: int = 3

    def __post_init__(self) -> None:
        if isinstance(self.radius, bool) or not isinstance(self.radius, int):
            raise ValueError("inpainting radius must be an integer from 1 to 10")
        if not 1 <= self.radius <= 10:
            raise ValueError("inpainting radius must be from 1 to 10")


@dataclass(frozen=True)
class VideoMetadata:
    """Media facts needed by the core and presentation layers."""

    path: Path
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float
    container: str

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("video dimensions must be positive")
        if not isfinite(self.fps) or self.fps <= 0:
            raise ValueError("video FPS must be positive and finite")
        if self.frame_count < 0:
            raise ValueError("video frame count must be nonnegative")
        if not isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError("video duration must be nonnegative and finite")


class JobState(StrEnum):
    """Lifecycle states shared by queued processing jobs."""

    NEEDS_REGION = "needs-region"
    READY = "ready"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True)
class ProgressEvent:
    """A scheduler-independent progress notification."""

    job_id: str
    state: JobState
    frames_done: int
    frames_total: int | None
    message: str = ""

    def __post_init__(self) -> None:
        if self.frames_done < 0 or (self.frames_total is not None and self.frames_total < 0):
            raise ValueError("progress frame counts must be nonnegative")


@dataclass(frozen=True)
class PreviewRequest:
    """Input for a cancellable preview worker."""

    path: Path
    timestamp_ms: int
    region: NormalizedRegion
    options: ProcessingOptions
    sequence: int

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0:
            raise ValueError("preview timestamp must be nonnegative")
        if self.sequence < 0:
            raise ValueError("preview sequence must be nonnegative")


@dataclass(frozen=True)
class PreviewResult:
    """Paired source and cleaned frames produced by a preview worker."""

    sequence: int
    timestamp_ms: int
    source_frame: NDArray[np.uint8]
    cleaned_frame: NDArray[np.uint8]

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("preview sequence must be nonnegative")
        if self.timestamp_ms < 0:
            raise ValueError("preview timestamp must be nonnegative")


@dataclass(frozen=True)
class ExportRequest:
    """Input for a complete or preview-capped media export."""

    input_path: Path
    output_path: Path
    region: NormalizedRegion
    options: ProcessingOptions
    preview_seconds: float | None = None
    performance: PerformanceMode = PerformanceMode.BALANCED
    format_policy: FormatPolicy = FormatPolicy.ORIGINAL
    concurrent_exports: int = 1

    def __post_init__(self) -> None:
        if self.preview_seconds is not None and (
            not isfinite(self.preview_seconds) or self.preview_seconds <= 0
        ):
            raise ValueError("preview duration must be positive and finite")
        if (
            isinstance(self.concurrent_exports, bool)
            or not isinstance(self.concurrent_exports, int)
            or self.concurrent_exports < 1
        ):
            raise ValueError("concurrent exports must be a positive integer")
