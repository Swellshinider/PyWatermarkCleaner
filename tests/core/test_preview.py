from __future__ import annotations

from pathlib import Path
from threading import Event

import numpy as np
import pytest

from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.models import PreviewRequest, PreviewResult, ProcessingOptions
from pywatermarkcleaner.core.preview import LatestPreviewCoordinator, PreviewService


def request(sequence: int, *, path: str = "clip.mp4") -> PreviewRequest:
    return PreviewRequest(
        Path(path),
        250,
        NormalizedRegion(0.25, 0.25, 0.5, 0.5),
        ProcessingOptions(),
        sequence,
    )


class StaticReader:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame

    def read_frame(self, path: Path, timestamp_ms: int) -> np.ndarray:
        return self.frame.copy()


@pytest.mark.parametrize(
    ("shape", "expected_shape"),
    [((720, 1920, 3), (480, 1280, 3)), ((1920, 720, 3), (1280, 480, 3))],
)
def test_preview_scales_long_edge_and_inpaints_the_scaled_frame(
    shape: tuple[int, int, int], expected_shape: tuple[int, int, int]
) -> None:
    source = np.zeros(shape, dtype=np.uint8)
    observed: list[tuple[tuple[int, ...], NormalizedRegion]] = []

    def clean(
        frame: np.ndarray, region: NormalizedRegion, options: ProcessingOptions
    ) -> np.ndarray:
        observed.append((frame.shape, region))
        result = frame.copy()
        result[:] = 9
        return result

    result = PreviewService(StaticReader(source), inpaint_operation=clean).render(request(4))

    assert result.sequence == 4
    assert result.timestamp_ms == 250
    assert result.source_frame.shape == expected_shape
    assert result.cleaned_frame.shape == expected_shape
    assert np.all(result.cleaned_frame == 9)
    assert observed == [(expected_shape, request(4).region)]


def test_preview_keeps_frames_at_or_below_the_limit_unchanged() -> None:
    source = np.zeros((600, 800, 3), dtype=np.uint8)
    result = PreviewService(StaticReader(source)).render(request(1))

    assert result.source_frame.shape == source.shape
    assert result.cleaned_frame.shape == source.shape


class BlockingPreviewService:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.rendered: list[int] = []
        self.fail_sequences: set[int] = set()

    def render(self, preview_request: PreviewRequest) -> PreviewResult:
        self.rendered.append(preview_request.sequence)
        self.started.set()
        self.release.wait(timeout=2)
        if preview_request.sequence in self.fail_sequences:
            raise RuntimeError(f"failure {preview_request.sequence}")
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        return PreviewResult(
            preview_request.sequence,
            preview_request.timestamp_ms,
            frame,
            frame.copy(),
        )


def test_latest_preview_discards_stale_result_and_renders_newest_pending_request() -> None:
    service = BlockingPreviewService()
    delivered: list[int] = []
    errors: list[str] = []
    completed = Event()

    def receive(result: PreviewResult) -> None:
        delivered.append(result.sequence)
        completed.set()

    coordinator = LatestPreviewCoordinator(
        service,
        receive,
        lambda error: errors.append(str(error)),
    )

    coordinator.submit(request(1))
    assert service.started.wait(timeout=1)
    coordinator.submit(request(2))
    coordinator.submit(request(3))
    service.release.set()
    assert completed.wait(timeout=1)
    coordinator.close()

    assert service.rendered == [1, 3]
    assert delivered == [3]
    assert errors == []


def test_latest_preview_discards_stale_error() -> None:
    service = BlockingPreviewService()
    service.fail_sequences.add(1)
    delivered: list[int] = []
    errors: list[str] = []
    completed = Event()

    def receive(result: PreviewResult) -> None:
        delivered.append(result.sequence)
        completed.set()

    coordinator = LatestPreviewCoordinator(
        service,
        receive,
        lambda error: errors.append(str(error)),
    )

    coordinator.submit(request(1))
    assert service.started.wait(timeout=1)
    coordinator.submit(request(2))
    service.release.set()
    assert completed.wait(timeout=1)
    coordinator.close()

    assert delivered == [2]
    assert errors == []


def test_latest_preview_close_prevents_callbacks() -> None:
    service = BlockingPreviewService()
    delivered: list[int] = []
    errors: list[str] = []
    coordinator = LatestPreviewCoordinator(
        service,
        lambda result: delivered.append(result.sequence),
        lambda error: errors.append(str(error)),
    )

    coordinator.submit(request(1))
    assert service.started.wait(timeout=1)
    coordinator.close(wait=False)
    service.release.set()

    assert delivered == []
    assert errors == []
    with pytest.raises(RuntimeError, match="closed"):
        coordinator.submit(request(2))
