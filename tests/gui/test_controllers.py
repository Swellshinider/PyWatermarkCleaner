from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from threading import Event, get_ident

import numpy as np

from pywatermarkcleaner.core.cancellation import CancelledError
from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.models import (
    JobState,
    PreviewResult,
    ProgressEvent,
    VideoMetadata,
)
from pywatermarkcleaner.gui.export_controller import ExportController
from pywatermarkcleaner.gui.preview_controller import PreviewController
from pywatermarkcleaner.gui.queue import QueueModel


def metadata(path: Path) -> VideoMetadata:
    return VideoMetadata(path.resolve(), 320, 180, 25.0, 250, 10.0, "mp4")


def ready_model(tmp_path: Path, count: int = 1) -> QueueModel:
    model = QueueModel()
    for number in range(count):
        path = tmp_path / f"clip-{number}.mp4"
        path.touch()
        model.add_metadata(metadata(path))
        model.set_region(number, NormalizedRegion(0.1, 0.1, 0.2, 0.2))
    return model


class FakeCoordinator:
    def __init__(self, on_result, on_error) -> None:
        self.requests = []
        self.on_result = on_result
        self.on_error = on_error
        self.closed = False

    def submit(self, request) -> None:
        self.requests.append(request)

    def close(self, *, wait: bool = True) -> None:
        self.closed = True


def test_preview_sequences_and_suppresses_stale_results(qtbot, tmp_path: Path) -> None:
    holder = {}

    def factory(on_result, on_error):
        holder["coordinator"] = FakeCoordinator(on_result, on_error)
        return holder["coordinator"]

    controller = PreviewController(coordinator_factory=factory)
    item = ready_model(tmp_path).item(0)
    controller.select_item(item)
    controller.set_timestamp(100)
    controller.set_timestamp(200)
    controller.set_method("navier-stokes")
    controller.set_radius(5)
    requests = holder["coordinator"].requests
    sequences = [request.sequence for request in requests]
    assert sequences == sorted(set(sequences))
    assert requests[-1].timestamp_ms == 200
    assert requests[-1].options.radius == 5
    assert requests[-1].options.method.value == "navier-stokes"

    seen = []
    controller.frame_ready.connect(lambda *_args: seen.append(_args[-1]))
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    holder["coordinator"].on_result(PreviewResult(1, 100, frame, frame))
    holder["coordinator"].on_result(PreviewResult(requests[-1].sequence, 200, frame, frame))
    qtbot.waitUntil(lambda: seen == [200])
    controller.close()
    assert holder["coordinator"].closed


def test_silent_playback_submits_newest_source_timestamps(qtbot, tmp_path: Path) -> None:
    holder = {}

    def factory(on_result, on_error):
        holder["coordinator"] = FakeCoordinator(on_result, on_error)
        return holder["coordinator"]

    controller = PreviewController(coordinator_factory=factory)
    controller.select_item(ready_model(tmp_path).item(0))
    controller.play()
    qtbot.waitUntil(lambda: controller.timestamp_ms >= 80, timeout=1000)
    controller.pause()
    requests = holder["coordinator"].requests
    assert requests[-1].timestamp_ms == controller.timestamp_ms
    assert controller.audio_enabled is False
    controller.close()


def test_select_without_region_loads_source_frame_off_gui_thread(qtbot, tmp_path: Path) -> None:
    main_thread = get_ident()
    worker_threads: list[int] = []
    frame = np.zeros((18, 32, 3), dtype=np.uint8)

    class Reader:
        def read_frame(self, _path: Path, _timestamp: int):
            worker_threads.append(get_ident())
            return frame

    controller = PreviewController(reader=Reader())
    model = QueueModel()
    source = tmp_path / "plain.mp4"
    source.touch()
    model.add_metadata(metadata(source))
    with qtbot.waitSignal(controller.frame_ready, timeout=2000) as ready:
        controller.select_item(model.item(0))
    assert ready.args[0] == source.resolve()
    assert ready.args[1] is frame
    assert ready.args[2] is None
    assert worker_threads and worker_threads[0] != main_thread
    controller.close()


def test_source_only_preview_respects_1280_long_edge(qtbot, tmp_path: Path) -> None:
    frame = np.zeros((700, 1400, 3), dtype=np.uint8)

    class Reader:
        def read_frame(self, _path: Path, _timestamp: int):
            return frame

    controller = PreviewController(reader=Reader())
    model = QueueModel()
    source = tmp_path / "large.mp4"
    source.touch()
    model.add_metadata(metadata(source))
    with qtbot.waitSignal(controller.frame_ready, timeout=2000) as ready:
        controller.select_item(model.item(0))
    assert ready.args[0] == source.resolve()
    assert ready.args[1].shape == (640, 1280, 3)
    controller.close()


def test_cleaned_results_are_owned_by_current_selection(qtbot, tmp_path: Path) -> None:
    holder = {}

    def factory(on_result, on_error):
        holder["coordinator"] = FakeCoordinator(on_result, on_error)
        return holder["coordinator"]

    model = ready_model(tmp_path, 2)
    controller = PreviewController(coordinator_factory=factory)
    seen: list[Path] = []
    controller.frame_ready.connect(lambda path, *_args: seen.append(path))
    controller.select_item(model.item(0))
    first = holder["coordinator"].requests[-1]
    controller.select_item(model.item(1))
    second = holder["coordinator"].requests[-1]
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    holder["coordinator"].on_result(PreviewResult(first.sequence, first.timestamp_ms, frame, frame))
    holder["coordinator"].on_result(
        PreviewResult(second.sequence, second.timestamp_ms, frame, frame)
    )
    qtbot.waitUntil(lambda: seen == [model.item(1).metadata.path])
    controller.close()


def test_selecting_no_region_invalidates_older_cleaned_result(qtbot, tmp_path: Path) -> None:
    holder = {}
    release = Event()

    def factory(on_result, on_error):
        holder["coordinator"] = FakeCoordinator(on_result, on_error)
        return holder["coordinator"]

    class Reader:
        def read_frame(self, _path: Path, _timestamp: int):
            release.wait(1)
            return np.zeros((8, 8, 3), dtype=np.uint8)

    model = ready_model(tmp_path)
    plain = tmp_path / "plain-selection.mp4"
    plain.touch()
    model.add_metadata(metadata(plain))
    controller = PreviewController(coordinator_factory=factory, reader=Reader())
    seen: list[Path] = []
    controller.frame_ready.connect(lambda path, *_args: seen.append(path))
    controller.select_item(model.item(0))
    old_request = holder["coordinator"].requests[-1]
    controller.select_item(model.item(1))
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    holder["coordinator"].on_result(PreviewResult(old_request.sequence, 0, frame, frame))
    qtbot.wait(50)
    assert seen == []
    release.set()
    controller.close()


def test_source_results_are_owned_by_current_selection(qtbot, tmp_path: Path) -> None:
    first_release = Event()
    second_release = Event()
    entered: dict[str, Event] = {"first.mp4": Event(), "second.mp4": Event()}

    class Reader:
        def read_frame(self, path: Path, _timestamp: int):
            entered[path.name].set()
            (first_release if path.name == "first.mp4" else second_release).wait(1)
            value = 1 if path.name == "first.mp4" else 2
            return np.full((8, 8, 3), value, dtype=np.uint8)

    model = QueueModel()
    for name in ("first.mp4", "second.mp4"):
        path = tmp_path / name
        path.touch()
        model.add_metadata(metadata(path))
    controller = PreviewController(reader=Reader())
    seen: list[tuple[Path, int]] = []
    controller.frame_ready.connect(
        lambda path, frame, *_args: seen.append((path, int(frame[0, 0, 0])))
    )
    controller.select_item(model.item(0))
    assert entered["first.mp4"].wait(1)
    controller.select_item(model.item(1))
    assert entered["second.mp4"].wait(1)
    second_release.set()
    qtbot.waitUntil(lambda: seen == [(model.item(1).metadata.path, 2)])
    first_release.set()
    qtbot.wait(50)
    assert seen == [(model.item(1).metadata.path, 2)]
    controller.clear_selection()
    assert controller.selected_path is None
    assert not controller.is_playing()
    controller.close()


class FakeScheduledJob:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.future: Future[Path] = Future()


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[tuple[object, object, FakeScheduledJob]] = []
        self.canceled = []
        self.cancel_all_called = False
        self.shutdown_calls = []

    def submit(self, request, on_progress=None):
        job = FakeScheduledJob(f"job-{len(self.jobs) + 1}")
        self.jobs.append((request, on_progress, job))
        return job

    def cancel(self, job_id: str) -> bool:
        self.canceled.append(job_id)
        return True

    def cancel_all(self) -> None:
        self.cancel_all_called = True

    def shutdown(self, *, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


def test_export_progress_completion_failure_retry_and_cancel(qtbot, tmp_path: Path) -> None:
    model = ready_model(tmp_path, 2)
    scheduler = FakeScheduler()
    controller = ExportController(
        model,
        exporter=object(),
        scheduler_factory=lambda _exporter, _workers: scheduler,
        output_resolver=lambda path, folder: folder / f"{path.stem}_cleaned.mp4",
    )
    controller.start(tmp_path / "out", workers=1)
    assert [model.item(i).state for i in range(2)] == [JobState.QUEUED] * 2

    _request, progress, first = scheduler.jobs[0]
    progress(ProgressEvent(first.job_id, JobState.PROCESSING, 5, 10, "Cleaning frames"))
    qtbot.waitUntil(lambda: model.item(0).progress == 50)
    output = tmp_path / "out" / "clip-0_cleaned.mp4"
    first.future.set_result(output)
    qtbot.waitUntil(lambda: model.item(0).state == JobState.COMPLETED)

    second = scheduler.jobs[1][2]
    second.future.set_exception(RuntimeError("disk full; choose another folder"))
    qtbot.waitUntil(lambda: model.item(1).state == JobState.FAILED)
    assert "choose another folder" in model.item(1).error

    controller.retry(1)
    assert len(scheduler.jobs) == 3
    controller.cancel_item(1)
    assert scheduler.canceled[-1] == scheduler.jobs[-1][2].job_id
    controller.cancel_all()
    assert scheduler.cancel_all_called
    controller.close()
    assert scheduler.shutdown_calls[-1] is False


def test_export_maps_cancellation_and_ignores_results_after_close(qtbot, tmp_path: Path) -> None:
    model = ready_model(tmp_path)
    scheduler = FakeScheduler()
    controller = ExportController(
        model,
        exporter=object(),
        scheduler_factory=lambda *_args: scheduler,
    )
    controller.start(tmp_path, workers=1)
    future = scheduler.jobs[0][2].future
    future.set_exception(CancelledError("canceled"))
    qtbot.waitUntil(lambda: model.item(0).state == JobState.CANCELED)

    model.update_job(0, state=JobState.READY, error="")
    controller.retry(0)
    controller.close()
    scheduler.jobs[-1][2].future.set_result(tmp_path / "late.mp4")
    qtbot.wait(50)
    assert model.item(0).state == JobState.QUEUED
