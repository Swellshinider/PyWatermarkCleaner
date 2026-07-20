from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.models import (
    ExportRequest,
    InpaintMethod,
    JobState,
    PreviewRequest,
    PreviewResult,
    ProcessingOptions,
    ProgressEvent,
    VideoMetadata,
)


def test_inpaint_method_values_and_processing_defaults() -> None:
    assert InpaintMethod.TELEA.value == "telea"
    assert InpaintMethod.NAVIER_STOKES.value == "navier-stokes"
    assert ProcessingOptions() == ProcessingOptions(InpaintMethod.TELEA, 3)


@pytest.mark.parametrize("radius", [True, 1.5, 0, 11])
def test_processing_options_require_integer_radius_in_supported_range(radius: object) -> None:
    with pytest.raises(ValueError, match="radius"):
        ProcessingOptions(radius=radius)  # type: ignore[arg-type]


def test_processing_options_are_frozen() -> None:
    options = ProcessingOptions()

    with pytest.raises(FrozenInstanceError):
        options.radius = 4  # type: ignore[misc]


def _metadata(**changes: object) -> VideoMetadata:
    values: dict[str, object] = {
        "path": Path("input.mov"),
        "width": 1920,
        "height": 1080,
        "fps": 29.97,
        "frame_count": 300,
        "duration_seconds": 10.01,
        "container": "mov",
    }
    values.update(changes)
    return VideoMetadata(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"width": 0},
        {"height": -1},
        {"fps": 0.0},
        {"fps": float("inf")},
        {"frame_count": -1},
        {"duration_seconds": -0.1},
        {"duration_seconds": float("nan")},
    ],
)
def test_video_metadata_validates_numeric_fields(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _metadata(**changes)


def test_video_metadata_accepts_zero_count_and_duration() -> None:
    assert _metadata(frame_count=0, duration_seconds=0.0).frame_count == 0


def test_job_state_values_are_stable() -> None:
    assert [state.value for state in JobState] == [
        "needs-region",
        "ready",
        "queued",
        "processing",
        "completed",
        "failed",
        "canceled",
    ]


@pytest.mark.parametrize(
    ("frames_done", "frames_total"),
    [(-1, None), (0, -1)],
)
def test_progress_event_rejects_negative_progress(
    frames_done: int, frames_total: int | None
) -> None:
    with pytest.raises(ValueError, match="progress"):
        ProgressEvent("job-1", JobState.PROCESSING, frames_done, frames_total)


def test_progress_event_permits_unknown_total_and_default_message() -> None:
    event = ProgressEvent("job-1", JobState.QUEUED, 0, None)

    assert event.frames_total is None
    assert event.message == ""


def test_preview_worker_contracts_hold_frames_and_request_values() -> None:
    region = NormalizedRegion(0.1, 0.2, 0.3, 0.4)
    options = ProcessingOptions(InpaintMethod.NAVIER_STOKES, 5)
    request = PreviewRequest(Path("clip.mp4"), 250, region, options, 7)
    source = np.zeros((2, 2, 3), dtype=np.uint8)
    cleaned = np.ones_like(source)
    result = PreviewResult(7, 250, source, cleaned)

    assert request.region is region
    assert request.options is options
    assert result.source_frame is source
    assert result.cleaned_frame is cleaned


@pytest.mark.parametrize(
    "factory",
    [
        lambda: PreviewRequest(
            Path("clip.mp4"),
            -1,
            NormalizedRegion(0.0, 0.0, 0.5, 0.5),
            ProcessingOptions(),
            0,
        ),
        lambda: PreviewRequest(
            Path("clip.mp4"),
            0,
            NormalizedRegion(0.0, 0.0, 0.5, 0.5),
            ProcessingOptions(),
            -1,
        ),
        lambda: PreviewResult(
            -1,
            0,
            np.empty((0, 0, 3), dtype=np.uint8),
            np.empty((0, 0, 3), dtype=np.uint8),
        ),
        lambda: PreviewResult(
            0,
            -1,
            np.empty((0, 0, 3), dtype=np.uint8),
            np.empty((0, 0, 3), dtype=np.uint8),
        ),
    ],
)
def test_preview_contracts_reject_negative_timing_or_sequence(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize("preview_seconds", [0.0, -1.0, float("inf"), float("nan")])
def test_export_request_requires_positive_finite_preview_cap(preview_seconds: float) -> None:
    with pytest.raises(ValueError, match="preview"):
        ExportRequest(
            Path("in.mp4"),
            Path("out.mp4"),
            NormalizedRegion(0.0, 0.0, 0.5, 0.5),
            ProcessingOptions(),
            preview_seconds,
        )


def test_export_request_permits_no_preview_cap_and_five_seconds() -> None:
    base = (
        Path("in.mp4"),
        Path("out.mp4"),
        NormalizedRegion(0.0, 0.0, 0.5, 0.5),
        ProcessingOptions(),
    )

    assert ExportRequest(*base).preview_seconds is None
    assert ExportRequest(*base, preview_seconds=5.0).preview_seconds == 5.0
