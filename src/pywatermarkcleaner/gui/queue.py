"""Qt queue state and asynchronous media probing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from threading import RLock
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRunnable,
    Qt,
    QThreadPool,
    Signal,
    Slot,
)

from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.media import OpenCVMediaReader
from pywatermarkcleaner.core.models import JobState, VideoMetadata

STATE_LABELS = {
    JobState.NEEDS_REGION: "Needs region",
    JobState.READY: "Ready",
    JobState.QUEUED: "Queued",
    JobState.PROCESSING: "Processing",
    JobState.COMPLETED: "Completed",
    JobState.FAILED: "Failed",
    JobState.CANCELED: "Canceled",
}
_ROOT_INDEX = QModelIndex()


class QueueRole(IntEnum):
    PATH = int(Qt.ItemDataRole.UserRole) + 1
    METADATA = PATH + 1
    STATE = PATH + 2
    STATE_LABEL = PATH + 3
    PROGRESS = PATH + 4
    REGION = PATH + 5
    OUTPUT_PATH = PATH + 6
    ERROR = PATH + 7
    TIMELINE_POSITION = PATH + 8


@dataclass
class QueueItem:
    metadata: VideoMetadata
    timeline_position_ms: int = 0
    region: NormalizedRegion | None = None
    state: JobState = JobState.NEEDS_REGION
    progress: int = 0
    output_path: Path | None = None
    error: str = ""


class QueueModel(QAbstractListModel):
    """One mutable presentation record per canonical input path."""

    status_message = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[QueueItem] = []
        self._paths: dict[Path, int] = {}

    def rowCount(  # noqa: N802
        self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return {
            int(QueueRole.PATH): QByteArray(b"path"),
            int(QueueRole.METADATA): QByteArray(b"metadata"),
            int(QueueRole.STATE): QByteArray(b"state"),
            int(QueueRole.STATE_LABEL): QByteArray(b"stateLabel"),
            int(QueueRole.PROGRESS): QByteArray(b"progress"),
            int(QueueRole.REGION): QByteArray(b"region"),
            int(QueueRole.OUTPUT_PATH): QByteArray(b"outputPath"),
            int(QueueRole.ERROR): QByteArray(b"error"),
            int(QueueRole.TIMELINE_POSITION): QByteArray(b"timelinePosition"),
        }

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == int(Qt.ItemDataRole.DisplayRole):
            return item.metadata.path.name
        if role == int(Qt.ItemDataRole.AccessibleTextRole):
            parts = [item.metadata.path.name, STATE_LABELS[item.state]]
            if item.error:
                parts.append(item.error)
            return ". ".join(parts)
        values = {
            int(QueueRole.PATH): item.metadata.path,
            int(QueueRole.METADATA): item.metadata,
            int(QueueRole.STATE): item.state,
            int(QueueRole.STATE_LABEL): STATE_LABELS[item.state],
            int(QueueRole.PROGRESS): item.progress,
            int(QueueRole.REGION): item.region,
            int(QueueRole.OUTPUT_PATH): item.output_path,
            int(QueueRole.ERROR): item.error,
            int(QueueRole.TIMELINE_POSITION): item.timeline_position_ms,
        }
        return values.get(role)

    def add_metadata(self, metadata: VideoMetadata) -> bool:
        canonical = Path(metadata.path).resolve()
        if canonical in self._paths:
            self.status_message.emit(f"{canonical.name} is already in the queue.")
            return False
        canonical_metadata = VideoMetadata(
            canonical,
            metadata.width,
            metadata.height,
            metadata.fps,
            metadata.frame_count,
            metadata.duration_seconds,
            metadata.container,
        )
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(QueueItem(canonical_metadata))
        self._paths[canonical] = row
        self.endInsertRows()
        return True

    def item(self, row: int) -> QueueItem:
        return self._items[row]

    def items(self) -> tuple[QueueItem, ...]:
        return tuple(self._items)

    def index_for_path(self, path: Path) -> int | None:
        return self._paths.get(Path(path).resolve())

    @staticmethod
    def _valid_region(metadata: VideoMetadata, region: NormalizedRegion | None) -> bool:
        if region is None:
            return False
        try:
            pixels = region.to_pixels(metadata.width, metadata.height)
        except ValueError:
            return False
        return pixels.width >= 2 and pixels.height >= 2

    def set_region(self, row: int, region: NormalizedRegion | None) -> bool:
        item = self._items[row]
        active = item.state in {JobState.QUEUED, JobState.PROCESSING}
        valid = self._valid_region(item.metadata, region)
        item.region = region if valid else None
        if not valid and not active:
            item.state = JobState.NEEDS_REGION
        elif valid and not active:
            item.state = JobState.READY
        self._changed(row, QueueRole.REGION, QueueRole.STATE, QueueRole.STATE_LABEL)
        return valid

    def set_timeline_position(self, row: int, timestamp_ms: int) -> None:
        self._items[row].timeline_position_ms = max(0, timestamp_ms)
        self._changed(row, QueueRole.TIMELINE_POSITION)

    def update_job(
        self,
        row: int,
        *,
        state: JobState | None = None,
        progress: int | None = None,
        output_path: Path | None = None,
        error: str | None = None,
    ) -> None:
        item = self._items[row]
        if state is not None:
            item.state = state
        if progress is not None:
            item.progress = max(0, min(100, progress))
        if output_path is not None:
            item.output_path = Path(output_path)
        if error is not None:
            item.error = error
        self._changed(
            row,
            QueueRole.STATE,
            QueueRole.STATE_LABEL,
            QueueRole.PROGRESS,
            QueueRole.OUTPUT_PATH,
            QueueRole.ERROR,
        )

    def remove_row(self, row: int) -> None:
        self.beginRemoveRows(QModelIndex(), row, row)
        self._items.pop(row)
        self.endRemoveRows()
        self._paths = {item.metadata.path: number for number, item in enumerate(self._items)}

    def all_ready(self) -> bool:
        return bool(self._items) and all(
            self._valid_region(item.metadata, item.region) for item in self._items
        )

    def _changed(self, row: int, *roles: QueueRole) -> None:
        index = self.index(row)
        self.dataChanged.emit(index, index, [int(role) for role in roles])


class _ProbeSignals(QObject):
    succeeded = Signal(object, object)
    failed = Signal(object, str)
    finished = Signal(object)


class _ProbeTask(QRunnable):
    def __init__(self, reader: object, path: Path) -> None:
        super().__init__()
        self.reader = reader
        self.path = path
        self.signals = _ProbeSignals()

    @Slot()
    def run(self) -> None:
        try:
            metadata = self.reader.probe(self.path)  # type: ignore[attr-defined]
        except Exception as error:
            self.signals.failed.emit(self.path, str(error))
        else:
            self.signals.succeeded.emit(self.path, metadata)
        finally:
            self.signals.finished.emit(self.path)


class ProbeController(QObject):
    """Probe dropped files on the global Qt worker pool."""

    file_added = Signal(object)
    message = Signal(str)

    def __init__(
        self,
        model: QueueModel,
        *,
        reader: object | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.model = model
        self.reader = reader or OpenCVMediaReader()
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._pending: set[Path] = set()
        self._tasks: set[_ProbeTask] = set()
        self._closed = False
        self._lock = RLock()

    def add_paths(self, paths: list[Path] | tuple[Path, ...]) -> None:
        for raw_path in paths:
            path = Path(raw_path).resolve()
            with self._lock:
                duplicate = self.model.index_for_path(path) is not None or path in self._pending
                if self._closed:
                    return
                if duplicate:
                    self.message.emit(f"{path.name} is already in the queue.")
                    continue
                self._pending.add(path)
            task = _ProbeTask(self.reader, path)
            task.signals.succeeded.connect(self._succeeded, Qt.ConnectionType.QueuedConnection)
            task.signals.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
            task.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
            self._tasks.add(task)
            self.thread_pool.start(task)

    @Slot(object, object)
    def _succeeded(self, path: Path, metadata: VideoMetadata) -> None:
        if self._closed:
            return
        if self.model.add_metadata(metadata):
            self.file_added.emit(path)

    @Slot(object, str)
    def _failed(self, path: Path, detail: str) -> None:
        if not self._closed:
            self.message.emit(f"Could not add {path.name}: {detail}")

    @Slot(object)
    def _finished(self, path: Path) -> None:
        with self._lock:
            self._pending.discard(Path(path))
            self._tasks = {task for task in self._tasks if task.path != Path(path)}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending.clear()
