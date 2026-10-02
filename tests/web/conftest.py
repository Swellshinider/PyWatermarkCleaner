from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from pywatermarkcleaner.core.cancellation import CancellationToken, CancelledError
from pywatermarkcleaner.core.models import ExportRequest, JobState, ProgressEvent
from pywatermarkcleaner.web.server import COOKIE_NAME, create_app

TOKEN = "test-token"
PORT = 8765
BASE_URL = f"http://127.0.0.1:{PORT}"


class FakeExporter:
    """Writes a stub output; blocks on ``gate`` so tests can cancel or inspect running jobs."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.fail = False
        self.requests: list[ExportRequest] = []

    def export(
        self,
        request: ExportRequest,
        token: CancellationToken,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> Path:
        self.requests.append(request)
        if on_progress is not None:
            on_progress(ProgressEvent("", JobState.PROCESSING, 1, 2))
        while not self.gate.wait(0.02):
            token.raise_if_cancelled()
        token.raise_if_cancelled()
        if self.fail:
            raise RuntimeError("disk full")
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"x")
        return request.output_path


def make_video(path: Path, *, width: int = 64, height: int = 48, frames: int = 30) -> Path:
    """Write a small mp4 whose frame brightness encodes its index."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), 10.0, (width, height))
    for index in range(frames):
        writer.write(np.full((height, width, 3), 8 * index, dtype=np.uint8))
    writer.release()
    return path


@pytest.fixture
def video(tmp_path: Path) -> Path:
    return make_video(tmp_path / "clip.mp4")


@pytest.fixture
def exporter() -> FakeExporter:
    return FakeExporter()


@pytest.fixture
def picked(video: Path) -> list[str]:
    return [str(video)]


@pytest.fixture
def client(tmp_path: Path, exporter: FakeExporter, picked: list[str]) -> Iterator[TestClient]:
    app = create_app(
        token=TOKEN,
        port=PORT,
        settings_path=tmp_path / "config" / "settings.json",
        exporter=exporter,
        file_picker=lambda: picked,
        folder_picker=lambda: str(tmp_path / "out"),
        reveal=lambda path: None,
    )
    http = TestClient(app, base_url=BASE_URL)
    http.cookies.set(COOKIE_NAME, TOKEN)
    http.app_state = app.state  # type: ignore[attr-defined]
    yield http
    app.state.jobs.close()
    app.state.session.close()


@pytest.fixture
def added(client: TestClient) -> dict[str, Any]:
    """The single picked video, as an item dict."""
    items = client.post("/api/videos/pick").json()["items"]
    assert len(items) == 1
    return items[0]  # type: ignore[no-any-return]


def wait_for(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)


__all__ = ["CancelledError"]
