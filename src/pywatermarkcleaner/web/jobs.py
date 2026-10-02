"""Export scheduling for the web session; a port of the retired desktop export controller."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from pywatermarkcleaner.core.cancellation import CancelledError
from pywatermarkcleaner.core.export import VideoExporter, resolve_export_path
from pywatermarkcleaner.core.models import (
    ExportRequest,
    FormatPolicy,
    InpaintMethod,
    JobState,
    PerformanceMode,
    ProcessingOptions,
    ProgressEvent,
)
from pywatermarkcleaner.core.scheduler import JobScheduler

from .session import Item, Session

_RESUBMITTABLE = {JobState.READY, JobState.NEEDS_REGION, JobState.FAILED, JobState.CANCELED}
_PROGRESS_INTERVAL = 0.25  # seconds between progress events per item (~4 Hz)


class JobController:
    """Submit ready items to a scheduler built with the worker count chosen at each start."""

    def __init__(
        self,
        session: Session,
        *,
        exporter: Any | None = None,
        scheduler_factory: Callable[..., Any] = JobScheduler,
    ) -> None:
        self.session = session
        self.exporter = exporter or VideoExporter()
        self.scheduler_factory = scheduler_factory
        self._scheduler: Any | None = None
        self._jobs: dict[str, str] = {}  # item id -> scheduler job id
        self._last_emit: dict[str, float] = {}

    def has_active_jobs(self) -> bool:
        with self.session.lock:
            return bool(self._jobs)

    def start(self) -> None:
        """Submit every item that is ready, failed or canceled and has a region."""
        with self.session.lock:
            self._refresh_scheduler()
            for item in list(self.session.items):
                if item.region is not None and item.state in _RESUBMITTABLE:
                    self._submit(item)
            self._sync_running()
        self.session.publish_state()

    def retry(self, item: Item) -> None:
        with self.session.lock:
            self._refresh_scheduler()
            item.state, item.progress, item.error = JobState.READY, 0, ""
            self._submit(item)
            self._sync_running()
        self.session.publish_state()

    def cancel_item(self, item: Item) -> bool:
        with self.session.lock:
            job_id = self._jobs.get(item.id)
            if job_id is None or self._scheduler is None:
                return False
            return bool(self._scheduler.cancel(job_id))

    def cancel_all(self) -> None:
        with self.session.lock:
            if self._scheduler is not None:
                self._scheduler.cancel_all()
        self.session.log("Canceling all exports")

    def close(self) -> None:
        with self.session.lock:
            scheduler, self._scheduler = self._scheduler, None
        if scheduler is not None:
            scheduler.cancel_all()
            scheduler.shutdown(wait=False)

    # -- internals ---------------------------------------------------------
    def _refresh_scheduler(self) -> None:
        """Rebuild the scheduler while idle so the current worker setting takes effect."""
        if self._scheduler is not None and self._jobs:
            return  # jobs still running keep the pool they were submitted to
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
        self._scheduler = self.scheduler_factory(self.exporter, self.session.settings.workers)

    def _sync_running(self) -> None:
        self.session.running = bool(self._jobs)

    def _submit(self, item: Item) -> None:
        if self._scheduler is None or item.region is None:
            return
        settings = self.session.settings
        policy = FormatPolicy(settings.format_policy)
        output_path = resolve_export_path(item.path, Path(settings.output_folder), policy)
        request = ExportRequest(
            item.path,
            output_path,
            item.region,
            ProcessingOptions(InpaintMethod(settings.method), settings.radius),
            performance=PerformanceMode(settings.performance),
            format_policy=policy,
        )
        item_id = item.id

        def report(event: ProgressEvent) -> None:
            self._on_progress(item_id, event)

        job = self._scheduler.submit(request, on_progress=report)
        self._jobs[item_id] = job.job_id
        item.state, item.progress, item.output_path, item.error = (
            JobState.QUEUED,
            0,
            output_path,
            "",
        )
        self.session.publish_item(item)
        self.session.log(f"Queued {item.path.name}")

        def done(future: Future[Path]) -> None:
            self._on_completed(item_id, job.job_id, future)

        job.future.add_done_callback(done)

    def _on_progress(self, item_id: str, event: ProgressEvent) -> None:
        with self.session.lock:
            item = self.session.get(item_id)
            if item is None or item_id not in self._jobs:
                return
            changed = event.state != item.state
            if event.frames_total:
                item.progress = int(min(100, event.frames_done * 100 / event.frames_total))
            item.state = event.state
            now = time.monotonic()
            due = changed or now - self._last_emit.get(item_id, 0.0) >= _PROGRESS_INTERVAL
            if due:
                self._last_emit[item_id] = now
        if due:
            self.session.publish_item(item)
        if event.message:
            self.session.log(f"{item.path.name}: {event.message}")

    def _on_completed(self, item_id: str, job_id: str, future: Future[Path]) -> None:
        with self.session.lock:
            if self._jobs.get(item_id) == job_id:
                del self._jobs[item_id]
            self._last_emit.pop(item_id, None)
            item = self.session.get(item_id)
            if item is None:
                self._sync_running()
                return
            try:
                output = future.result()
            except CancelledError:
                item.state, item.error = JobState.CANCELED, "Export canceled."
                message = f"Canceled {item.path.name}"
            except Exception as error:
                remedy = str(error) or "Export failed. Choose another output folder and retry."
                item.state, item.error = JobState.FAILED, remedy
                message = f"Failed {item.path.name}: {remedy}"
            else:
                item.state, item.progress, item.output_path, item.error = (
                    JobState.COMPLETED,
                    100,
                    output,
                    "",
                )
                message = f"Completed {item.path.name}"
            self._sync_running()
        self.session.publish_item(item)
        self.session.log(message)
        self.session.publish_state()
