from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import QStyleOptionViewItem

from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.models import JobState, VideoMetadata
from pywatermarkcleaner.gui import theme
from pywatermarkcleaner.gui.app import main
from pywatermarkcleaner.gui.diagnostics import build_diagnostics
from pywatermarkcleaner.gui.preview_controller import PreviewController
from pywatermarkcleaner.gui.queue import QueueModel
from pywatermarkcleaner.gui.settings import AppSettings
from pywatermarkcleaner.gui.theme import assets_directory, validate_required_assets
from pywatermarkcleaner.gui.window import MainWindow, QueueDelegate


def metadata(path: Path, width: int = 100, height: int = 50) -> VideoMetadata:
    return VideoMetadata(path.resolve(), width, height, 25.0, 250, 10.0, "mp4")


def test_settings_defaults_valid_ranges_and_persistence(tmp_path: Path) -> None:
    settings = AppSettings(default_output=tmp_path / "Videos")
    assert settings.method == "telea"
    assert settings.radius == 3
    assert settings.workers == 1
    assert settings.set_radius(7)
    assert not settings.set_radius(20)
    assert settings.set_workers(settings.worker_max)
    assert not settings.set_workers(settings.worker_max + 1)
    assert settings.set_output_folder(tmp_path / "Exports")

    restored = AppSettings(default_output=tmp_path / "Elsewhere")
    assert restored.radius == 7
    assert restored.workers == settings.worker_max
    assert restored.output_folder == (tmp_path / "Exports").resolve()


def test_window_selection_restore_apply_all_clean_enablement_and_output_binding(
    qtbot, tmp_path: Path
) -> None:
    model = QueueModel()
    for name, size in (("wide.mp4", (200, 100)), ("small.mp4", (100, 100))):
        path = tmp_path / name
        path.touch()
        model.add_metadata(metadata(path, *size))
    window = MainWindow(model=model, preview_controller=None, export_controller=None)
    qtbot.addWidget(window)
    window.show()
    assert not window.clean_button.isEnabled()

    region = NormalizedRegion(0.1, 0.2, 0.3, 0.4)
    model.set_region(0, region)
    model.set_timeline_position(0, 1234)
    window.select_row(0)
    assert window.timeline.value() == 1234
    assert window.canvas.region == region
    window.apply_region_to_all()
    assert model.item(1).region == region
    assert model.item(1).region.to_pixels(100, 100).width == 30
    assert window.clean_button.isEnabled()

    selected = tmp_path / "Chosen"
    window.set_output_folder(selected)
    assert window.output_edit.text() == str(selected.resolve())
    assert QSettings().value("output_folder") == str(selected.resolve())
    window.close()


def test_window_responsive_drawers_activity_and_accessible_controls(qtbot) -> None:
    window = MainWindow(preview_controller=None, export_controller=None)
    qtbot.addWidget(window)
    window.resize(959, 700)
    window.show()
    qtbot.waitUntil(lambda: window.queue_toggle.isVisible())
    assert not window.queue_panel.isVisible()
    assert not window.inspector_panel.isVisible()
    center_width = window.center_panel.width()
    qtbot.mouseClick(window.queue_toggle, Qt.MouseButton.LeftButton)
    assert window.queue_panel.isVisible()
    assert window.center_panel.width() == center_width
    qtbot.mouseClick(window.inspector_toggle, Qt.MouseButton.LeftButton)
    assert window.inspector_panel.isVisible()
    assert window.center_panel.width() == center_width

    window.append_activity("Ready for videos")
    assert "Ready for videos" in window.activity_log.toPlainText()
    qtbot.mouseClick(window.activity_toggle, Qt.MouseButton.LeftButton)
    assert window.activity_log.isVisible()
    assert window.timeline.accessibleName() == "Video timeline"
    assert window.clean_button.minimumHeight() >= 44
    window.resize(1100, 700)
    qtbot.waitUntil(lambda: window.inspector_panel.isVisible())
    window.close()


class WindowExportFake(QObject):
    activity = Signal(str)
    batch_changed = Signal(int, str)
    all_finished = Signal()

    def __init__(self, *, synchronous_cancel: bool = False) -> None:
        super().__init__()
        self.canceled_rows: list[int] = []
        self.active = True
        self.synchronous_cancel = synchronous_cancel
        self.closed = False

    def cancel_item(self, row: int) -> bool:
        self.canceled_rows.append(row)
        return True

    def cancel_all(self) -> None:
        if self.synchronous_cancel:
            self.active = False
            self.all_finished.emit()

    def has_active_jobs(self) -> bool:
        return self.active

    def finish(self) -> None:
        self.active = False
        self.all_finished.emit()

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("active_state", [JobState.QUEUED, JobState.PROCESSING])
def test_active_selection_locks_request_controls_and_keeps_cancel(
    qtbot, tmp_path: Path, active_state: JobState
) -> None:
    model = QueueModel()
    source = tmp_path / "active.mp4"
    source.touch()
    model.add_metadata(metadata(source))
    model.set_region(0, NormalizedRegion(0.1, 0.1, 0.2, 0.2))
    exporter = WindowExportFake()
    window = MainWindow(model=model, preview_controller=None, export_controller=exporter)
    qtbot.addWidget(window)
    window.show()
    window.select_row(0)
    assert not window.cancel_item_button.isVisible()

    model.update_job(0, state=active_state, progress=20)
    qtbot.waitUntil(window.cancel_item_button.isVisible)
    assert window.cancel_item_button.isEnabled()
    assert not window.remove_button.isEnabled()
    for control in (
        window.canvas,
        window.x_spin,
        window.y_spin,
        window.width_spin,
        window.height_spin,
        window.apply_all_button,
        window.method_combo,
        window.radius_spin,
    ):
        assert not control.isEnabled()

    qtbot.mouseClick(window.cancel_item_button, Qt.MouseButton.LeftButton)
    assert exporter.canceled_rows == [0]
    model.update_job(0, state=JobState.CANCELED)
    qtbot.waitUntil(lambda: window.canvas.isEnabled())
    assert window.remove_button.isEnabled()
    assert not window.cancel_item_button.isVisible()
    for control in (
        window.x_spin,
        window.y_spin,
        window.width_spin,
        window.height_spin,
        window.apply_all_button,
        window.method_combo,
        window.radius_spin,
    ):
        assert control.isEnabled()
    exporter.active = False
    window.close()


def test_removing_selection_resets_preview_transport_and_canvas(qtbot, tmp_path: Path) -> None:
    class Coordinator:
        def submit(self, _request) -> None:
            pass

        def close(self, *, wait: bool = True) -> None:
            pass

    controller = PreviewController(coordinator_factory=lambda _on_result, _on_error: Coordinator())
    model = QueueModel()
    source = tmp_path / "selected.mp4"
    source.touch()
    model.add_metadata(metadata(source))
    model.set_region(0, NormalizedRegion(0.1, 0.1, 0.2, 0.2))
    window = MainWindow(model=model, preview_controller=controller, export_controller=None)
    qtbot.addWidget(window)
    window.show()
    window.select_row(0)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    window.canvas.set_frames(frame, frame)
    controller.play()
    qtbot.mouseClick(window.remove_button, Qt.MouseButton.LeftButton)

    assert controller.selected_path is None
    assert not controller.is_playing()
    assert window.timeline.maximum() == 0
    assert window.canvas._source is None
    window.close()


def test_apply_all_rejects_region_too_small_for_mixed_resolution(qtbot, tmp_path: Path) -> None:
    model = QueueModel()
    for name, size in (("large.mp4", (200, 200)), ("small.mp4", (50, 50))):
        path = tmp_path / name
        path.touch()
        model.add_metadata(metadata(path, *size))
    window = MainWindow(model=model, preview_controller=None, export_controller=None)
    qtbot.addWidget(window)
    window.show()
    model.set_region(0, NormalizedRegion(0.0, 0.0, 0.01, 0.01))
    window.select_row(0)
    window.apply_region_to_all()

    assert model.item(0).state == JobState.READY
    assert model.item(1).state == JobState.NEEDS_REGION
    assert model.item(1).region is None
    assert not window.clean_button.isEnabled()
    window.close()


def test_failed_delegate_allocates_remedy_height_and_accessible_text(tmp_path: Path) -> None:
    model = QueueModel()
    source = tmp_path / "failed.mp4"
    source.touch()
    model.add_metadata(metadata(source))
    model.update_job(0, state=JobState.FAILED, error="Choose another output folder and retry.")
    delegate = QueueDelegate()
    option = QStyleOptionViewItem()

    failed_height = delegate.sizeHint(option, model.index(0)).height()
    model.update_job(0, state=JobState.READY, error="")
    normal_height = delegate.sizeHint(option, model.index(0)).height()
    assert failed_height > normal_height
    model.update_job(0, state=JobState.FAILED, error="Choose another output folder and retry.")
    assert "retry" in model.data(model.index(0), Qt.ItemDataRole.AccessibleTextRole).lower()


def test_close_completes_when_cancel_finishes_synchronously(qtbot) -> None:
    exporter = WindowExportFake(synchronous_cancel=True)
    window = MainWindow(preview_controller=None, export_controller=exporter)
    qtbot.addWidget(window)
    window.show()
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert exporter.closed


def test_close_waits_for_asynchronous_cancellation(qtbot) -> None:
    exporter = WindowExportFake()
    window = MainWindow(preview_controller=None, export_controller=exporter)
    qtbot.addWidget(window)
    window.show()
    window.close()
    assert window.isVisible()
    exporter.finish()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert exporter.closed


def test_diagnostics_sanitizes_paths_and_includes_versions(tmp_path: Path) -> None:
    secret = tmp_path / "private" / "customer-watermark.mp4"
    item_metadata = metadata(secret)
    text = build_diagnostics([item_metadata], ["disk full at frame 12"])
    assert str(secret.parent) not in text
    assert "customer-watermark.mp4" in text
    for label in ("App:", "Python:", "Qt:", "OpenCV:", "FFmpeg:", "Platform:"):
        assert label in text
    assert "disk full at frame 12" in text


def test_smoke_test_creates_and_closes_window(qtbot, capsys, tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.touch()
    result = main(
        ["--smoke-test"],
        ffmpeg_resolver=lambda: ffmpeg,
        validate_assets=True,
    )
    captured = capsys.readouterr()
    assert result == 0
    assert captured.out.strip() == "PyWatermarkCleaner GUI smoke test: OK"


def test_required_font_license_and_icon_assets_are_valid() -> None:
    validate_required_assets()
    assets = assets_directory()
    for name in (
        "BarlowSemiCondensed-SemiBold.ttf",
        "AtkinsonHyperlegible-Regular.ttf",
        "IBMPlexMono-Regular.ttf",
        "OFL-BarlowSemiCondensed.txt",
        "OFL-AtkinsonHyperlegible.txt",
        "OFL-IBMPlexMono.txt",
    ):
        assert (assets / "fonts" / name).stat().st_size > 100


def test_asset_validation_rejects_missing_font_bundle(monkeypatch, tmp_path: Path) -> None:
    icon_dir = tmp_path / "icons"
    icon_dir.mkdir()
    shutil.copy2(
        assets_directory() / "icons" / "repair-aperture.svg",
        icon_dir / "repair-aperture.svg",
    )
    monkeypatch.setattr(theme, "assets_directory", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="font"):
        validate_required_assets()
