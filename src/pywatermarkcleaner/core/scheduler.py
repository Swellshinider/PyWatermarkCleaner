"""Bounded, failure-isolated background export scheduling."""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import NamedTuple, Protocol, cast

from .cancellation import CancellationToken
from .models import ExportRequest, ProgressEvent


def max_allowed_workers() -> int:
    """Return the supported export concurrency for the current machine."""
    return max(1, min(4, os.cpu_count() or 1))


class Exporter(Protocol):
    def export(
        self,
        request: ExportRequest,
        token: CancellationToken,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> Path: ...


class ScheduledJob(NamedTuple):
    """Stable identity and result handle returned by scheduler submission."""

    job_id: str
    future: Future[Path]


class JobScheduler:
    """Run independent exports in a bounded thread pool with cooperative cancellation."""

    def __init__(self, exporter: Exporter, max_workers: int = 1) -> None:
        maximum = max_allowed_workers()
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or not 1 <= max_workers <= maximum
        ):
            raise ValueError(f"workers must be an integer from 1 to {maximum}")
        self._exporter = exporter
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="export")
        self._lock = RLock()
        self._next_id = 1
        self._closed = False
        self._jobs: dict[str, tuple[Future[Path], CancellationToken]] = {}

    def submit(
        self,
        request: ExportRequest,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> ScheduledJob:
        with self._lock:
            if self._closed:
                raise RuntimeError("job scheduler has been shut down")
            job_id = f"job-{self._next_id:06d}"
            self._next_id += 1
            token = CancellationToken()

            def report(event: ProgressEvent) -> None:
                if on_progress is not None:
                    on_progress(replace(event, job_id=job_id))

            future = cast(
                Future[Path],
                self._executor.submit(
                    self._exporter.export,
                    replace(request, concurrent_exports=self._max_workers),
                    token,
                    report,
                ),
            )
            self._jobs[job_id] = (future, token)

            def remove_completed(completed: Future[Path]) -> None:
                self._remove(job_id, completed)

            future.add_done_callback(remove_completed)
            return ScheduledJob(job_id, future)

    def _remove(self, job_id: str, future: Future[Path]) -> None:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is not None and current[0] is future:
                del self._jobs[job_id]

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of an active or queued job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            token = job[1]
        token.cancel()
        return True

    def cancel_all(self) -> None:
        """Request cancellation of every active or queued job."""
        with self._lock:
            tokens = [token for _, token in self._jobs.values()]
        for token in tokens:
            token.cancel()

    def shutdown(self, *, wait: bool = True) -> None:
        """Reject future submissions and release the owned executor."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait)

    def __enter__(self) -> JobScheduler:
        return self

    def __exit__(self, *args: object) -> None:
        self.shutdown()
