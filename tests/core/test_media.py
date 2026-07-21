from pathlib import Path

import cv2
import numpy as np
import pytest

from pywatermarkcleaner.core.exceptions import MediaError
from pywatermarkcleaner.core.media import OpenCVMediaReader


class FakeCapture:
    def __init__(
        self,
        *,
        opened: bool = True,
        frame: np.ndarray | None = None,
        properties: dict[int, float] | None = None,
    ) -> None:
        self.opened = opened
        self.frame = frame
        self.properties = properties or {}
        self.seek_ms: float | None = None
        self.released = False

    def isOpened(self) -> bool:
        return self.opened

    def get(self, property_id: int) -> float:
        return self.properties.get(property_id, 0.0)

    def set(self, property_id: int, value: float) -> bool:
        if property_id == cv2.CAP_PROP_POS_MSEC:
            self.seek_ms = value
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        return self.frame is not None, self.frame

    def release(self) -> None:
        self.released = True


def test_probe_returns_validated_metadata_and_releases_capture(tmp_path: Path) -> None:
    media = tmp_path / "Sample.MOV"
    media.touch()
    capture = FakeCapture(
        properties={
            cv2.CAP_PROP_FRAME_WIDTH: 1920,
            cv2.CAP_PROP_FRAME_HEIGHT: 1080,
            cv2.CAP_PROP_FPS: 29.97,
            cv2.CAP_PROP_FRAME_COUNT: 300,
        }
    )
    reader = OpenCVMediaReader(capture_factory=lambda _: capture)

    metadata = reader.probe(media)

    assert metadata.path == media
    assert metadata.width == 1920
    assert metadata.height == 1080
    assert metadata.fps == pytest.approx(29.97)
    assert metadata.frame_count == 300
    assert metadata.duration_seconds == pytest.approx(300 / 29.97)
    assert metadata.container == "mov"
    assert capture.released


@pytest.mark.parametrize("failure", ["missing", "closed", "bad-metadata"])
def test_probe_reports_actionable_media_errors(tmp_path: Path, failure: str) -> None:
    media = tmp_path / "broken.mp4"
    if failure != "missing":
        media.touch()
    capture = FakeCapture(
        opened=failure != "closed",
        properties={
            cv2.CAP_PROP_FRAME_WIDTH: 0 if failure == "bad-metadata" else 10,
            cv2.CAP_PROP_FRAME_HEIGHT: 10,
            cv2.CAP_PROP_FPS: 24,
            cv2.CAP_PROP_FRAME_COUNT: 1,
        },
    )
    reader = OpenCVMediaReader(capture_factory=lambda _: capture)

    with pytest.raises(MediaError, match="broken.mp4"):
        reader.probe(media)


def test_read_frame_seeks_by_timestamp_and_returns_bgr_uint8(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.touch()
    frame = np.full((4, 6, 3), 17, dtype=np.uint8)
    capture = FakeCapture(frame=frame)
    reader = OpenCVMediaReader(capture_factory=lambda _: capture)

    result = reader.read_frame(media, 1250)

    assert capture.seek_ms == 1250
    assert result is frame
    assert result.dtype == np.uint8
    assert result.shape == (4, 6, 3)
    assert capture.released


@pytest.mark.parametrize(
    "frame",
    [None, np.zeros((2, 2), dtype=np.uint8), np.zeros((2, 2, 3), dtype=np.float32)],
)
def test_read_frame_rejects_decode_or_format_failures(
    tmp_path: Path, frame: np.ndarray | None
) -> None:
    media = tmp_path / "clip.mp4"
    media.touch()
    capture = FakeCapture(frame=frame)
    reader = OpenCVMediaReader(capture_factory=lambda _: capture)

    with pytest.raises(MediaError, match="125 ms"):
        reader.read_frame(media, 125)
