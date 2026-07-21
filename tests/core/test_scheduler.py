from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from threading import Event

import pytest

from pywatermarkcleaner.core.cancellation import CancellationToken, CancelledError
from pywatermarkcleaner.core.exceptions import ExportError
from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.models import ExportRequest, ProcessingOptions, ProgressEvent
from pywatermarkcleaner.core.scheduler import JobScheduler, max_allowed_workers


def request(name: str) -> ExportRequest:
    return ExportRequest(
        Path(f"{name}.mp4"),
        Path(f"{name}-out.mp4"),
        NormalizedRegion(0.0, 0.0, 0.5, 0.5),
        ProcessingOptions(),
    )


@pytest.mark.parametrize(("cpu_count", "expected"), [(None, 1), (0, 1), (1, 1), (3, 3), (32, 4)])
def test_max_allowed_workers_is_cpu_aware_and_bounded(
    cpu_count: int | None, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: cpu_count)

    assert max_allowed_workers() == expected


class ImmediateExporter:
    def export(
        self,
        export_request: ExportRequest,
        token: CancellationToken,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> Path:
        token.raise_if_cancelled()
        if export_request.input_path.stem == "fail":
            raise ExportError("isolated failure")
        return export_request.output_path


def test_scheduler_validates_worker_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 2)

    for workers in (0, 3, True):
        with pytest.raises(ValueError, match="workers"):
            JobScheduler(ImmediateExporter(), max_workers=workers)


def test_scheduler_assigns_stable_ids_and_failures_do_not_stop_other_jobs() -> None:
    scheduler = JobScheduler(ImmediateExporter(), max_workers=2)

    first = scheduler.submit(request("ok"))
    failed = scheduler.submit(request("fail"))
    third = scheduler.submit(request("also-ok"))

    assert first.job_id == "job-000001"
    assert failed.job_id == "job-000002"
    assert third.job_id == "job-000003"
    assert first.future.result(timeout=1) == Path("ok-out.mp4")
    with pytest.raises(ExportError, match="isolated failure"):
        failed.future.result(timeout=1)
    assert third.future.result(timeout=1) == Path("also-ok-out.mp4")
    assert not scheduler.cancel(first.job_id)
    scheduler.shutdown()


class BlockingExporter:
    def __init__(self) -> None:
        self.started: dict[str, Event] = {}
        self.release = Event()

    def export(
        self,
        export_request: ExportRequest,
        token: CancellationToken,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> Path:
        started = self.started.setdefault(export_request.input_path.stem, Event())
        started.set()
        while not self.release.wait(timeout=0.01):
            token.raise_if_cancelled()
        token.raise_if_cancelled()
        return export_request.output_path


def assert_cancelled(future: Future[Path]) -> None:
    with pytest.raises(CancelledError):
        future.result(timeout=1)


def test_scheduler_cancels_one_job_without_canceling_another() -> None:
    exporter = BlockingExporter()
    scheduler = JobScheduler(exporter, max_workers=2)
    canceled = scheduler.submit(request("cancel"))
    retained = scheduler.submit(request("retain"))
    assert exporter.started["cancel"].wait(timeout=1)
    assert exporter.started["retain"].wait(timeout=1)

    assert scheduler.cancel(canceled.job_id)
    assert_cancelled(canceled.future)
    exporter.release.set()

    assert retained.future.result(timeout=1) == Path("retain-out.mp4")
    scheduler.shutdown()


def test_scheduler_cancel_all_and_shutdown_close_submission() -> None:
    exporter = BlockingExporter()
    scheduler = JobScheduler(exporter, max_workers=2)
    first = scheduler.submit(request("one"))
    second = scheduler.submit(request("two"))
    assert exporter.started["one"].wait(timeout=1)
    assert exporter.started["two"].wait(timeout=1)

    scheduler.cancel_all()
    assert_cancelled(first.future)
    assert_cancelled(second.future)
    scheduler.shutdown()

    with pytest.raises(RuntimeError, match="shut down"):
        scheduler.submit(request("late"))
