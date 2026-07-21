"""Responsive three-pane desktop editing workbench."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Slot,
)
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QPainter,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListView,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pywatermarkcleaner.cli import default_output_directory
from pywatermarkcleaner.core.geometry import NormalizedRegion, PixelRegion
from pywatermarkcleaner.core.models import JobState

from .canvas import RepairCanvas
from .diagnostics import build_diagnostics
from .export_controller import ExportController
from .preview_controller import PreviewController
from .queue import STATE_LABELS, ProbeController, QueueModel, QueueRole
from .settings import AppSettings
from .theme import AMBER, CYAN, RAISED, TEXT, app_icon


def _timecode(milliseconds: int) -> str:
    seconds, millis = divmod(max(0, milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


class QueueDelegate(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        rect = option.rect
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, RAISED)
        metadata = index.data(int(QueueRole.METADATA))
        state = index.data(int(QueueRole.STATE))
        progress = int(index.data(int(QueueRole.PROGRESS)) or 0)
        painter.setPen(TEXT)
        painter.drawText(
            rect.adjusted(10, 7, -8, -42), Qt.AlignmentFlag.AlignLeft, metadata.path.name
        )
        painter.setPen("#96A9B3")
        details = (
            f"{metadata.width}×{metadata.height}  "
            f"{_timecode(round(metadata.duration_seconds * 1000))}"
        )
        painter.drawText(rect.adjusted(10, 27, -8, -22), Qt.AlignmentFlag.AlignLeft, details)
        painter.setPen(AMBER if state in {JobState.NEEDS_REGION, JobState.FAILED} else CYAN)
        painter.drawText(
            rect.adjusted(10, 48, -8, -4), Qt.AlignmentFlag.AlignLeft, STATE_LABELS[state]
        )
        error = str(index.data(int(QueueRole.ERROR)) or "")
        if error:
            painter.save()
            remedy_rect = rect.adjusted(10, 68, -10, -8)
            painter.setClipRect(remedy_rect)
            painter.setPen(AMBER)
            painter.drawText(
                remedy_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                | int(Qt.TextFlag.TextWordWrap),
                error,
            )
            painter.restore()
        if state in {JobState.QUEUED, JobState.PROCESSING}:
            bar = rect.adjusted(90, 54, -10, -10)
            painter.fillRect(bar, "#101A24")
            painter.fillRect(QRect(bar.x(), bar.y(), round(bar.width() * progress / 100), 3), CYAN)

    def sizeHint(
        self,
        _option: QStyleOptionViewItem,
        _index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        return QSize(220, 112 if _index.data(int(QueueRole.ERROR)) else 76)


_DEFAULT = object()


class _WorkbenchRoot(QWidget):
    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(640, 480)


class MainWindow(QMainWindow):
    """The precision-studio workbench and widget/controller lifetime boundary."""

    def __init__(
        self,
        *,
        model: QueueModel | None = None,
        preview_controller: PreviewController | None | object = _DEFAULT,
        export_controller: ExportController | None | object = _DEFAULT,
    ) -> None:
        super().__init__()
        self.setWindowTitle("PyWatermarkCleaner")
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.resize(1180, 760)
        self.model = model or QueueModel(self)
        self.probe_controller = ProbeController(self.model, parent=self)
        self.preview_controller = (
            PreviewController(parent=self)
            if preview_controller is _DEFAULT
            else cast(PreviewController | None, preview_controller)
        )
        self.export_controller = (
            ExportController(self.model, parent=self)
            if export_controller is _DEFAULT
            else cast(ExportController | None, export_controller)
        )
        self.settings = AppSettings(default_output=default_output_directory())
        self._selected_row: int | None = None
        self._syncing_region = False
        self._compact = False
        self._queue_drawer_open = False
        self._inspector_drawer_open = False
        self._allow_close = False
        self._waiting_for_close = False
        self._build_ui()
        self._connect_signals()
        self._update_clean_enabled()

    def _build_ui(self) -> None:
        root = _WorkbenchRoot()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        drawer_bar = QWidget()
        drawer_layout = QHBoxLayout(drawer_bar)
        drawer_layout.setContentsMargins(10, 6, 10, 6)
        self.queue_toggle = QPushButton("Videos")
        self.queue_toggle.setCheckable(True)
        self.queue_toggle.setAccessibleName("Toggle video queue")
        self.inspector_toggle = QPushButton("Inspector")
        self.inspector_toggle.setCheckable(True)
        self.inspector_toggle.setAccessibleName("Toggle inspector")
        drawer_layout.addWidget(self.queue_toggle)
        drawer_layout.addStretch()
        drawer_layout.addWidget(self.inspector_toggle)
        self.drawer_bar = drawer_bar

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(1)
        body_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.body = body
        self.body_layout = body_layout
        self.queue_panel = self._build_queue_panel()
        self.center_panel = self._build_center_panel()
        self.inspector_panel = self._build_inspector_panel()
        body_layout.addWidget(self.queue_panel)
        body_layout.addWidget(self.center_panel, 1)
        body_layout.addWidget(self.inspector_panel)

        self.batch_bar = self._build_batch_bar()
        root_layout.addWidget(drawer_bar)
        root_layout.addWidget(body, 1)
        root_layout.addWidget(self.batch_bar)
        self.setCentralWidget(root)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(640, 480)

    def _build_queue_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("queuePanel")
        panel.setFixedWidth(240)
        layout = QVBoxLayout(panel)
        heading = QLabel("VIDEO QUEUE")
        heading.setObjectName("heading")
        self.add_button = QPushButton("Add videos")
        self.add_button.setMinimumHeight(44)
        self.add_button.setAccessibleName("Add videos")
        self.queue_view = QListView()
        self.queue_view.setModel(self.model)
        self.queue_view.setItemDelegate(QueueDelegate(self.queue_view))
        self.queue_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.queue_view.setAccessibleName("Video queue")
        actions = QHBoxLayout()
        self.remove_button = QPushButton("Remove")
        self.retry_button = QPushButton("Retry")
        self.cancel_item_button = QPushButton("Cancel")
        self.show_button = QPushButton("Show in folder")
        self.remove_button.setAccessibleName("Remove selected video")
        self.retry_button.setAccessibleName("Retry selected export")
        self.cancel_item_button.setAccessibleName("Cancel selected export")
        self.show_button.setAccessibleName("Show completed export in folder")
        actions.addWidget(self.remove_button)
        actions.addWidget(self.retry_button)
        actions.addWidget(self.cancel_item_button)
        self.cancel_item_button.hide()
        layout.addWidget(heading)
        layout.addWidget(self.add_button)
        layout.addWidget(self.queue_view, 1)
        layout.addLayout(actions)
        layout.addWidget(self.show_button)
        return panel

    def _build_center_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 8)
        self.status_label = QLabel("Drop videos here or choose files")
        self.status_label.setAccessibleName("Status message")
        self.canvas = RepairCanvas()
        transport = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.setMinimumSize(64, 44)
        self.play_button.setAccessibleName("Play silent preview")
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.setAccessibleName("Video timeline")
        self.timeline.setAccessibleDescription("Scrub through the selected video")
        self.timecode = QLabel("00:00:00.000 / 00:00:00.000")
        self.timecode.setStyleSheet("font-family: 'IBM Plex Mono', Consolas, monospace;")
        transport.addWidget(self.play_button)
        transport.addWidget(self.timeline, 1)
        transport.addWidget(self.timecode)
        layout.addWidget(self.status_label)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(transport)
        return panel

    def _spin(self, name: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 999999)
        spin.setAccessibleName(name)
        return spin

    def _build_inspector_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("inspectorPanel")
        panel.setFixedWidth(280)
        layout = QVBoxLayout(panel)
        heading = QLabel("REPAIR INSPECTOR")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        region_group = QGroupBox("Region in source pixels")
        region_layout = QVBoxLayout(region_group)
        self.x_spin = self._spin("Region x position")
        self.y_spin = self._spin("Region y position")
        self.width_spin = self._spin("Region width")
        self.height_spin = self._spin("Region height")
        for label_text, spin in (
            ("X", self.x_spin),
            ("Y", self.y_spin),
            ("Width", self.width_spin),
            ("Height", self.height_spin),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label_text))
            row.addWidget(spin)
            region_layout.addLayout(row)
        self.apply_all_button = QPushButton("Apply to all")
        self.apply_all_button.setMinimumHeight(44)
        region_layout.addWidget(self.apply_all_button)
        layout.addWidget(region_group)

        self.method_combo = QComboBox()
        self.method_combo.addItem("Telea", "telea")
        self.method_combo.addItem("Navier–Stokes", "navier-stokes")
        self.method_combo.setAccessibleName("Repair method")
        self.method_combo.setCurrentIndex(max(0, self.method_combo.findData(self.settings.method)))
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(1, 10)
        self.radius_spin.setValue(self.settings.radius)
        self.radius_spin.setAccessibleName("Inpainting radius")
        layout.addWidget(QLabel("Method"))
        layout.addWidget(self.method_combo)
        layout.addWidget(QLabel("Radius"))
        layout.addWidget(self.radius_spin)

        layout.addWidget(QLabel("Output folder"))
        output_row = QHBoxLayout()
        self.output_edit = QLineEdit(str(self.settings.output_folder))
        self.output_edit.setReadOnly(True)
        self.output_edit.setAccessibleName("Output folder")
        self.output_button = QPushButton("Choose")
        self.output_button.setAccessibleName("Choose output folder")
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_button)
        layout.addLayout(output_row)

        self.advanced_group = QGroupBox("Advanced")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(self.settings.advanced_expanded)
        advanced_layout = QVBoxLayout(self.advanced_group)
        self.advanced_body = QWidget()
        body_layout = QHBoxLayout(self.advanced_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(QLabel("Export workers"))
        self.worker_spin = QSpinBox()
        self.worker_spin.setRange(1, self.settings.worker_max)
        self.worker_spin.setValue(self.settings.workers)
        self.worker_spin.setAccessibleName("Export worker count")
        body_layout.addWidget(self.worker_spin)
        advanced_layout.addWidget(self.advanced_body)
        self.advanced_body.setVisible(self.settings.advanced_expanded)
        layout.addWidget(self.advanced_group)
        layout.addStretch()
        return panel

    def _build_batch_bar(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("batchBar")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(12, 8, 12, 8)
        row = QHBoxLayout()
        self.clean_button = QPushButton("Clean videos")
        self.clean_button.setObjectName("primary")
        self.clean_button.setMinimumHeight(44)
        self.clean_button.setAccessibleName("Clean videos")
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 100)
        self.batch_progress.setAccessibleName("Batch export progress")
        self.batch_action = QLabel("Waiting for videos")
        self.cancel_all_button = QPushButton("Cancel all")
        self.cancel_all_button.setAccessibleName("Cancel all exports")
        self.activity_toggle = QPushButton("Activity log")
        self.activity_toggle.setCheckable(True)
        row.addWidget(self.clean_button)
        row.addWidget(self.batch_progress, 1)
        row.addWidget(self.batch_action)
        row.addWidget(self.cancel_all_button)
        row.addWidget(self.activity_toggle)
        self.activity_log = QTextEdit()
        self.activity_log.setReadOnly(True)
        self.activity_log.setMaximumHeight(130)
        self.activity_log.setAccessibleName("Activity log")
        self.activity_log.hide()
        self.copy_diagnostics_button = QPushButton("Copy diagnostics")
        self.copy_diagnostics_button.setAccessibleName("Copy diagnostics to clipboard")
        self.copy_diagnostics_button.hide()
        outer.addLayout(row)
        outer.addWidget(self.activity_log)
        outer.addWidget(self.copy_diagnostics_button, alignment=Qt.AlignmentFlag.AlignRight)
        return panel

    def _connect_signals(self) -> None:
        self.add_button.clicked.connect(self.choose_videos)
        self.model.status_message.connect(self.show_message)
        self.model.rowsInserted.connect(lambda *_args: self._update_clean_enabled())
        self.model.rowsRemoved.connect(lambda *_args: self._update_clean_enabled())
        self.model.dataChanged.connect(lambda *_args: self._update_clean_enabled())
        self.probe_controller.message.connect(self.show_message)
        self.probe_controller.file_added.connect(self._select_added)
        self.queue_view.selectionModel().currentChanged.connect(self._selection_changed)
        self.canvas.region_changed.connect(self._canvas_region_changed)
        self.timeline.valueChanged.connect(self._timeline_changed)
        self.play_button.clicked.connect(self._toggle_playback)
        self.apply_all_button.clicked.connect(self.apply_region_to_all)
        for spin in (self.x_spin, self.y_spin, self.width_spin, self.height_spin):
            spin.editingFinished.connect(self._numeric_region_changed)
        self.method_combo.currentIndexChanged.connect(self._method_changed)
        self.radius_spin.valueChanged.connect(self._radius_changed)
        self.worker_spin.valueChanged.connect(self.settings.set_workers)
        self.output_button.clicked.connect(self.choose_output_folder)
        self.advanced_group.toggled.connect(self._advanced_toggled)
        self.clean_button.clicked.connect(self.clean_videos)
        self.cancel_all_button.clicked.connect(self.cancel_all)
        self.retry_button.clicked.connect(self._retry_selected)
        self.cancel_item_button.clicked.connect(self._cancel_selected)
        self.remove_button.clicked.connect(self._remove_selected)
        self.show_button.clicked.connect(self._show_selected_output)
        self.activity_toggle.toggled.connect(self._activity_toggled)
        self.copy_diagnostics_button.clicked.connect(self.copy_diagnostics)
        self.queue_toggle.toggled.connect(self._toggle_queue_drawer)
        self.inspector_toggle.toggled.connect(self._toggle_inspector_drawer)
        if self.preview_controller is not None:
            self.preview_controller.frame_ready.connect(self._frame_ready)
            self.preview_controller.failed.connect(self.show_message)
            self.preview_controller.timestamp_changed.connect(self._preview_timestamp_changed)
        if self.export_controller is not None:
            self.export_controller.activity.connect(self.append_activity)
            self.export_controller.batch_changed.connect(self._batch_changed)

    @Slot(object)
    def _select_added(self, path: Path) -> None:
        row = self.model.index_for_path(path)
        if row is not None:
            self.select_row(row)
        self.show_message(f"Added {Path(path).name}. Draw the watermark region.")

    def select_row(self, row: int) -> None:
        index = self.model.index(row)
        self.queue_view.setCurrentIndex(index)
        self._selection_changed(index, QModelIndex())

    @Slot(QModelIndex, QModelIndex)
    def _selection_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            self._selected_row = None
            self.canvas.clear_frames()
            self.timeline.blockSignals(True)
            self.timeline.setRange(0, 0)
            self.timeline.setValue(0)
            self.timeline.blockSignals(False)
            self._update_timecode()
            self.play_button.setText("Play")
            self.play_button.setAccessibleName("Play silent preview")
            if self.preview_controller is not None:
                self.preview_controller.clear_selection()
            self._update_row_actions()
            return
        row = current.row()
        self._selected_row = row
        item = self.model.item(row)
        self.timeline.blockSignals(True)
        self.timeline.setRange(0, max(0, round(item.metadata.duration_seconds * 1000)))
        self.timeline.setValue(item.timeline_position_ms)
        self.timeline.blockSignals(False)
        self.play_button.setText("Play")
        self.play_button.setAccessibleName("Play silent preview")
        self.canvas.clear_frames()
        self.canvas._source_size = (item.metadata.width, item.metadata.height)
        self.canvas.set_region(item.region)
        self._set_region_spins(item.region)
        self._update_timecode()
        self._update_row_actions()
        if self.preview_controller is not None:
            self.preview_controller.select_item(item)

    def _set_region_spins(self, region: NormalizedRegion | None) -> None:
        if self._selected_row is None:
            return
        metadata = self.model.item(self._selected_row).metadata
        for spin in (self.x_spin, self.width_spin):
            spin.setMaximum(metadata.width)
        for spin in (self.y_spin, self.height_spin):
            spin.setMaximum(metadata.height)
        self.width_spin.setMinimum(2)
        self.height_spin.setMinimum(2)
        if region is None:
            values = (0, 0, 2, 2)
        else:
            pixels = region.to_pixels(metadata.width, metadata.height)
            values = (pixels.x, pixels.y, pixels.width, pixels.height)
        self._syncing_region = True
        for spin, value in zip(
            (self.x_spin, self.y_spin, self.width_spin, self.height_spin), values, strict=True
        ):
            spin.setValue(value)
        self._syncing_region = False

    @Slot(object)
    def _canvas_region_changed(self, region: NormalizedRegion) -> None:
        if self._selected_row is None:
            return
        if not self.model.set_region(self._selected_row, region):
            self.canvas.set_region(None)
            self.show_message("Region must cover at least 2×2 source pixels.")
            return
        self._set_region_spins(region)
        if self.preview_controller is not None:
            self.preview_controller.set_region(region)

    @Slot()
    def _numeric_region_changed(self) -> None:
        if self._syncing_region or self._selected_row is None:
            return
        metadata = self.model.item(self._selected_row).metadata
        try:
            region = PixelRegion(
                self.x_spin.value(),
                self.y_spin.value(),
                self.width_spin.value(),
                self.height_spin.value(),
            ).to_normalized(metadata.width, metadata.height)
        except ValueError as error:
            self.show_message(f"Region is not valid: {error}")
            return
        if self.model.set_region(self._selected_row, region):
            self.canvas.set_region(region)
            self._set_region_spins(region)
            if self.preview_controller is not None:
                self.preview_controller.set_region(region)
        else:
            self.canvas.set_region(None)
            self.show_message("Region must cover at least 2×2 source pixels.")

    def apply_region_to_all(self) -> None:
        if self._selected_row is None:
            return
        region = self.model.item(self._selected_row).region
        if region is None:
            self.show_message("Draw a valid region before applying it to all videos.")
            return
        rejected = 0
        for row in range(self.model.rowCount()):
            if not self.model.set_region(row, region):
                rejected += 1
        if rejected:
            self.show_message(
                f"Region was too small for {rejected} video(s); adjust it and apply again."
            )
        else:
            self.show_message("Applied the normalized region to every video.")

    @Slot(int)
    def _timeline_changed(self, value: int) -> None:
        if self._selected_row is None:
            return
        self.model.set_timeline_position(self._selected_row, value)
        self._update_timecode()
        if self.preview_controller is not None:
            self.preview_controller.set_timestamp(value)

    @Slot(int)
    def _preview_timestamp_changed(self, value: int) -> None:
        self.timeline.blockSignals(True)
        self.timeline.setValue(value)
        self.timeline.blockSignals(False)
        if self._selected_row is not None:
            self.model.set_timeline_position(self._selected_row, value)
        self._update_timecode()

    def _update_timecode(self) -> None:
        self.timecode.setText(
            f"{_timecode(self.timeline.value())} / {_timecode(self.timeline.maximum())}"
        )

    def _toggle_playback(self) -> None:
        if self.preview_controller is None:
            return
        if self.preview_controller.is_playing():
            self.preview_controller.pause()
            self.play_button.setText("Play")
            self.play_button.setAccessibleName("Play silent preview")
        else:
            self.preview_controller.play()
            self.play_button.setText("Pause")
            self.play_button.setAccessibleName("Pause silent preview")

    @Slot(object, object, object, int)
    def _frame_ready(self, path: Path, source: object, cleaned: object, _timestamp: int) -> None:
        if self._selected_row is None:
            return
        metadata = self.model.item(self._selected_row).metadata
        if metadata.path != Path(path):
            return
        self.canvas.set_frames(
            cast(NDArray[np.uint8], source),
            cast(NDArray[np.uint8] | None, cleaned),
            source_size=(metadata.width, metadata.height),
        )

    def _method_changed(self) -> None:
        method = str(self.method_combo.currentData())
        if self.settings.set_method(method) and self.preview_controller is not None:
            self.preview_controller.set_method(method)

    def _radius_changed(self, radius: int) -> None:
        if self.settings.set_radius(radius) and self.preview_controller is not None:
            self.preview_controller.set_radius(radius)

    def _advanced_toggled(self, expanded: bool) -> None:
        self.advanced_body.setVisible(expanded)
        self.settings.set_advanced_expanded(expanded)

    def _update_clean_enabled(self) -> None:
        self.clean_button.setEnabled(self.model.all_ready())
        self._update_row_actions()

    def _update_row_actions(self) -> None:
        if self._selected_row is None or self._selected_row >= self.model.rowCount():
            self.retry_button.setEnabled(False)
            self.cancel_item_button.hide()
            self.show_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            return
        item = self.model.item(self._selected_row)
        self.remove_button.setEnabled(item.state not in {JobState.QUEUED, JobState.PROCESSING})
        self.retry_button.setEnabled(item.state in {JobState.FAILED, JobState.CANCELED})
        active = item.state in {JobState.QUEUED, JobState.PROCESSING}
        self.cancel_item_button.setVisible(active)
        self.cancel_item_button.setEnabled(active)
        self.show_button.setEnabled(
            item.state == JobState.COMPLETED and item.output_path is not None
        )

    def clean_videos(self) -> None:
        if self.export_controller is None or not self.model.all_ready():
            return
        self.export_controller.start(
            self.settings.output_folder,
            workers=self.settings.workers,
            method=self.settings.method,
            radius=self.settings.radius,
        )

    def cancel_all(self) -> None:
        if self.export_controller is not None:
            self.export_controller.cancel_all()

    def _retry_selected(self) -> None:
        if self.export_controller is not None and self._selected_row is not None:
            self.export_controller.retry(self._selected_row)

    def _cancel_selected(self) -> None:
        if self.export_controller is not None and self._selected_row is not None:
            self.export_controller.cancel_item(self._selected_row)

    def _remove_selected(self) -> None:
        if self._selected_row is not None:
            row = self._selected_row
            self.queue_view.setCurrentIndex(QModelIndex())
            self._selected_row = None
            self.model.remove_row(row)

    def _show_selected_output(self) -> None:
        if self._selected_row is None:
            return
        output = self.model.item(self._selected_row).output_path
        if output is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output.parent)))

    def choose_videos(self) -> None:
        names, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "Add videos",
            str(Path.home()),
            "Video files (*.mp4 *.mov *.m4v *.mkv *.avi *.webm);;All files (*)",
        )
        if names:
            self.probe_controller.add_paths([Path(name) for name in names])

    def choose_output_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose output folder", str(self.settings.output_folder)
        )
        if selected:
            self.set_output_folder(Path(selected))

    def set_output_folder(self, folder: Path) -> None:
        if self.settings.set_output_folder(folder):
            self.output_edit.setText(str(self.settings.output_folder))

    def append_activity(self, text: str) -> None:
        self.activity_log.append(text)

    def show_message(self, text: str) -> None:
        self.status_label.setText(text)
        self.append_activity(text)

    def _activity_toggled(self, expanded: bool) -> None:
        self.activity_log.setVisible(expanded)
        self.copy_diagnostics_button.setVisible(expanded)

    def copy_diagnostics(self) -> None:
        text = build_diagnostics(
            [item.metadata for item in self.model.items()],
            [item.error for item in self.model.items() if item.error],
        )
        QApplication.clipboard().setText(text)
        self.show_message("Diagnostics copied.")

    @Slot(int, str)
    def _batch_changed(self, progress: int, action: str) -> None:
        self.batch_progress.setValue(progress)
        self.batch_action.setText(action)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_responsive()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        # Hide fixed side panes before the first layout pass can enforce their size hint.
        self._update_responsive()
        super().showEvent(event)

    def _update_responsive(self) -> None:
        compact = self.width() < 960
        if compact != self._compact:
            self._set_compact_layout(compact)
        self.drawer_bar.setVisible(compact)
        if compact:
            self.queue_panel.setVisible(self._queue_drawer_open)
            self.inspector_panel.setVisible(self._inspector_drawer_open)
            self._position_drawers()
        else:
            self.queue_panel.show()
            self.inspector_panel.show()

    def _set_compact_layout(self, compact: bool) -> None:
        self._compact = compact
        if compact:
            self.body_layout.removeWidget(self.queue_panel)
            self.body_layout.removeWidget(self.inspector_panel)
            self.queue_panel.setParent(self.body)
            self.inspector_panel.setParent(self.body)
        else:
            self.queue_panel.hide()
            self.inspector_panel.hide()
            self.body_layout.insertWidget(0, self.queue_panel)
            self.body_layout.addWidget(self.inspector_panel)
        self.body_layout.invalidate()
        self.body_layout.activate()

    def _position_drawers(self) -> None:
        height = self.body.height()
        self.queue_panel.setGeometry(0, 0, 240, height)
        self.inspector_panel.setGeometry(max(0, self.body.width() - 280), 0, 280, height)
        if self.queue_panel.isVisible():
            self.queue_panel.raise_()
        if self.inspector_panel.isVisible():
            self.inspector_panel.raise_()

    def _toggle_queue_drawer(self, open_: bool) -> None:
        self._queue_drawer_open = open_
        if open_:
            self._inspector_drawer_open = False
            self.inspector_toggle.blockSignals(True)
            self.inspector_toggle.setChecked(False)
            self.inspector_toggle.blockSignals(False)
        self._update_responsive()

    def _toggle_inspector_drawer(self, open_: bool) -> None:
        self._inspector_drawer_open = open_
        if open_:
            self._queue_drawer_open = False
            self.queue_toggle.blockSignals(True)
            self.queue_toggle.setChecked(False)
            self.queue_toggle.blockSignals(False)
        self._update_responsive()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.probe_controller.add_paths(paths)
            event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if (
            not self._allow_close
            and self.export_controller is not None
            and self.export_controller.has_active_jobs()
        ):
            event.ignore()
            self.show_message("Canceling active exports before closing…")
            if not self._waiting_for_close:
                self._waiting_for_close = True
                self.export_controller.all_finished.connect(self._finish_close)
            self.export_controller.cancel_all()
            if not self.export_controller.has_active_jobs():
                self._finish_close()
            return
        self._cleanup()
        event.accept()

    def _finish_close(self) -> None:
        if self._allow_close:
            return
        self._allow_close = True
        QTimer.singleShot(0, self.close)

    def _cleanup(self) -> None:
        self.probe_controller.close()
        if self.preview_controller is not None:
            self.preview_controller.close()
        if self.export_controller is not None:
            self.export_controller.close()
