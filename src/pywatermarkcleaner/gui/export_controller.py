"""Qt adapter for full-resolution scheduled exports."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot

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

from .queue import QueueModel


class _ExportBridge(QObject):
    progress = Signal(object, object)
    completed = Signal(object, object)


class ExportController(QObject):
    """Submit ready queue rows and bridge worker callbacks into the GUI thread."""

    activity = Signal(str)
    batch_changed = Signal(int, str)
    all_finished = Signal()

    def __init__(
        self,
        model: QueueModel,
        *,
        exporter: object | None = None,
        scheduler_factory: Callable[..., object] = JobScheduler,
        output_resolver: Callable[[Path, Path], Path] = resolve_export_path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.model = model
        self.exporter = exporter or VideoExporter()
        self.scheduler_factory = scheduler_factory
        self.output_resolver = output_resolver
        self._scheduler: object | None = None
        self._jobs: dict[Path, str] = {}
        self._futures: dict[Path, Future[Path]] = {}
        self._output_folder = Path.cwd()
        self._options = ProcessingOptions()
        self._performance = PerformanceMode.BALANCED
        self._format_policy = FormatPolicy.ORIGINAL
        self._closed = False
        self._bridge = _ExportBridge(self)
        self._bridge.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self._bridge.completed.connect(self._on_completed, Qt.ConnectionType.QueuedConnection)

    def start(
        self,
        output_folder: Path,
        *,
        workers: int,
        method: str | InpaintMethod = InpaintMethod.TELEA,
        radius: int = 3,
        performance: str | PerformanceMode = PerformanceMode.BALANCED,
        format_policy: str | FormatPolicy = FormatPolicy.ORIGINAL,
    ) -> None:
        if self._closed:
            return
        self._output_folder = Path(output_folder)
        selected_method = method if isinstance(method, InpaintMethod) else InpaintMethod(method)
        self._options = ProcessingOptions(selected_method, radius)
        self._performance = (
            performance
            if isinstance(performance, PerformanceMode)
            else PerformanceMode(performance)
        )
        self._format_policy = (
            format_policy
            if isinstance(format_policy, FormatPolicy)
            else FormatPolicy(format_policy)
        )
        if self._scheduler is None:
            self._scheduler = self.scheduler_factory(self.exporter, workers)
        for row, item in enumerate(self.model.items()):
            if item.region is not None and item.state in {
                JobState.READY,
                JobState.NEEDS_REGION,
                JobState.FAILED,
                JobState.CANCELED,
            }:
                self._submit(row)

    def _submit(self, row: int) -> None:
        if self._scheduler is None:
            return
        item = self.model.item(row)
        if item.region is None:
            return
        resolver_input = (
            item.metadata.path.with_suffix(".mp4")
            if self._format_policy is FormatPolicy.MP4
            else item.metadata.path
        )
        output_path = self.output_resolver(resolver_input, self._output_folder)
        request = ExportRequest(
            item.metadata.path,
            output_path,
            item.region,
            self._options,
            performance=self._performance,
            format_policy=self._format_policy,
        )
        path = item.metadata.path

        def report(event: ProgressEvent) -> None:
            self._bridge.progress.emit(path, event)

        job = self._scheduler.submit(request, on_progress=report)  # type: ignore[attr-defined]
        self._jobs[path] = job.job_id
        self._futures[path] = job.future
        self.model.update_job(
            row, state=JobState.QUEUED, progress=0, output_path=output_path, error=""
        )
        self.activity.emit(f"Queued {path.name}")

        def completed(future: Future[Path]) -> None:
            self._bridge.completed.emit(path, future)

        job.future.add_done_callback(completed)
        self._emit_batch("Queued exports")

    @Slot(object, object)
    def _on_progress(self, path: Path, event: ProgressEvent) -> None:
        if self._closed:
            return
        row = self.model.index_for_path(path)
        if row is None:
            return
        percent = 0
        if event.frames_total:
            percent = int(min(100, event.frames_done * 100 / event.frames_total))
        self.model.update_job(row, state=event.state, progress=percent)
        if event.message:
            self.activity.emit(f"{path.name}: {event.message}")
        self._emit_batch(event.message or "Processing")

    @Slot(object, object)
    def _on_completed(self, path: Path, future: Future[Path]) -> None:
        if self._closed:
            return
        row = self.model.index_for_path(path)
        self._jobs.pop(path, None)
        self._futures.pop(path, None)
        if row is None:
            return
        try:
            output = future.result()
        except CancelledError:
            self.model.update_job(row, state=JobState.CANCELED, error="Export canceled.")
            self.activity.emit(f"Canceled {path.name}")
        except Exception as error:
            remedy = str(error) or "Export failed. Choose another output folder and retry."
            self.model.update_job(row, state=JobState.FAILED, error=remedy)
            self.activity.emit(f"Failed {path.name}: {remedy}")
        else:
            self.model.update_job(
                row, state=JobState.COMPLETED, progress=100, output_path=output, error=""
            )
            self.activity.emit(f"Completed {path.name}")
        self._emit_batch("Export finished")
        if not self._jobs:
            self.all_finished.emit()

    def retry(self, row: int) -> None:
        item = self.model.item(row)
        if item.region is None:
            return
        self.model.update_job(row, state=JobState.READY, progress=0, error="")
        self._submit(row)

    def cancel_item(self, row: int) -> bool:
        item = self.model.item(row)
        job_id = self._jobs.get(item.metadata.path)
        if job_id is None or self._scheduler is None:
            return False
        return bool(self._scheduler.cancel(job_id))  # type: ignore[attr-defined]

    def cancel_all(self) -> None:
        if self._scheduler is not None:
            self._scheduler.cancel_all()  # type: ignore[attr-defined]
            self.activity.emit("Canceling all exports")

    def has_active_jobs(self) -> bool:
        return bool(self._jobs)

    def _emit_batch(self, action: str) -> None:
        items = self.model.items()
        if not items:
            self.batch_changed.emit(0, action)
            return
        percent = sum(item.progress for item in items) // len(items)
        self.batch_changed.emit(percent, action)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel_all()
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)  # type: ignore[attr-defined]
