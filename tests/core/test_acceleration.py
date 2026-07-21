from __future__ import annotations

import subprocess
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

import pywatermarkcleaner.core.export as export_module
from pywatermarkcleaner.core.cancellation import CancellationToken
from pywatermarkcleaner.core.export import VideoExporter, frame_worker_count
from pywatermarkcleaner.core.geometry import NormalizedRegion, PixelRegion
from pywatermarkcleaner.core.inpainting import create_mask, prepare_inpainting
from pywatermarkcleaner.core.models import (
    ExportRequest,
    FormatPolicy,
    InpaintMethod,
    PerformanceMode,
    ProcessingOptions,
    VideoMetadata,
)


@pytest.mark.parametrize("method", list(InpaintMethod))
@pytest.mark.parametrize("radius", range(1, 11))
@pytest.mark.parametrize(
    "region",
    [PixelRegion(0, 0, 8, 7), PixelRegion(31, 22, 9, 8), PixelRegion(13, 9, 10, 9)],
)
def test_cropped_inpainting_matches_full_frame(
    method: InpaintMethod, radius: int, region: PixelRegion
) -> None:
    frame = np.random.default_rng(radius).integers(0, 256, (30, 40, 3), dtype=np.uint8)
    original = frame.copy()
    options = ProcessingOptions(method, radius)
    algorithm = cv2.INPAINT_TELEA if method is InpaintMethod.TELEA else cv2.INPAINT_NS
    expected = cv2.inpaint(frame.copy(), create_mask(frame.shape, region), radius, algorithm)

    actual = prepare_inpainting(
        frame.shape, region.to_normalized(frame.shape[1], frame.shape[0]), options
    ).apply(frame)

    assert np.array_equal(actual, expected)
    assert np.array_equal(frame, original)


def test_frame_workers_are_bounded_by_cpu_exports_and_memory() -> None:
    assert frame_worker_count(1920, 1080, 2, logical_cpus=16) == 8
    assert frame_worker_count(7680, 4320, 1, logical_cpus=32) == 1
    assert frame_worker_count(640, 480, 4, logical_cpus=8) == 2


class _Reader:
    def probe(self, path: Path) -> VideoMetadata:
        return VideoMetadata(path, 4, 2, 25.0, 6, 0.24, path.suffix.lstrip("."))


class _Stdin:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> int:
        self.data.extend(data)
        return len(data)

    def close(self) -> None:
        pass


class _Process:
    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.stdin = _Stdin()
        self.stderr = BytesIO()
        Path(command[-1]).write_bytes(b"encoded")

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


def test_preflight_priority_parallel_order_cache_and_forced_mp4_audio(tmp_path: Path) -> None:
    export_module._PREFLIGHT_CACHE.clear()
    source = tmp_path / "source.webm"
    source.touch()
    output = tmp_path / "cleaned.mp4"
    frames = [np.full((2, 4, 3), value, dtype=np.uint8) for value in range(6)]
    preflights: list[list[str]] = []
    processes: list[_Process] = []

    def preflight(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        preflights.append(command)
        return subprocess.CompletedProcess(command, 0 if "h264_qsv" in command else 1)

    def process_factory(command: list[str], **_kwargs: Any) -> _Process:
        process = _Process(command)
        processes.append(process)
        return process

    def delayed(frame: np.ndarray, *_args: Any) -> np.ndarray:
        time.sleep((5 - int(frame[0, 0, 0])) * 0.002)
        return frame

    exporter = VideoExporter(
        media_reader=_Reader(),
        ffmpeg_resolver=lambda: Path("ffmpeg"),
        process_factory=process_factory,
        frame_source=lambda _path: iter(frames),
        inpaint_operation=delayed,
        preflight_runner=preflight,
    )
    request = ExportRequest(
        source,
        output,
        NormalizedRegion(0, 0, 0.5, 0.5),
        ProcessingOptions(),
        performance=PerformanceMode.BALANCED,
        format_policy=FormatPolicy.MP4,
    )

    exporter.export(request, CancellationToken())

    command = processes[0].command
    assert "h264_qsv" in command
    assert preflights[0][preflights[0].index("-c:v") + 1] == "h264_nvenc"
    assert preflights[1][preflights[1].index("-c:v") + 1] == "h264_qsv"
    assert processes[0].stdin.data == b"".join(frame.tobytes() for frame in frames)
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "192k"
