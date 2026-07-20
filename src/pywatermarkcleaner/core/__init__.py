"""Qt-free processing services for PyWatermarkCleaner."""

from .cancellation import CancellationToken, CancelledError
from .geometry import NormalizedRegion, PixelRegion
from .inpainting import create_mask, inpaint_frame
from .models import (
    ExportRequest,
    InpaintMethod,
    JobState,
    PreviewRequest,
    PreviewResult,
    ProcessingOptions,
    ProgressEvent,
    VideoMetadata,
)
from .output_paths import allocate_output_path

__all__ = [
    "CancelledError",
    "CancellationToken",
    "ExportRequest",
    "InpaintMethod",
    "JobState",
    "NormalizedRegion",
    "PixelRegion",
    "PreviewRequest",
    "PreviewResult",
    "ProcessingOptions",
    "ProgressEvent",
    "VideoMetadata",
    "allocate_output_path",
    "create_mask",
    "inpaint_frame",
]
