from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt

from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.models import VideoMetadata
from pywatermarkcleaner.gui.app import main
from pywatermarkcleaner.gui.diagnostics import build_diagnostics
from pywatermarkcleaner.gui.queue import QueueModel
from pywatermarkcleaner.gui.settings import AppSettings
from pywatermarkcleaner.gui.window import MainWindow


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
    qtbot.mouseClick(window.queue_toggle, Qt.MouseButton.LeftButton)
    assert window.queue_panel.isVisible()

    window.append_activity("Ready for videos")
    assert "Ready for videos" in window.activity_log.toPlainText()
    qtbot.mouseClick(window.activity_toggle, Qt.MouseButton.LeftButton)
    assert window.activity_log.isVisible()
    assert window.timeline.accessibleName() == "Video timeline"
    assert window.clean_button.minimumHeight() >= 44
    window.resize(1100, 700)
    qtbot.waitUntil(lambda: window.inspector_panel.isVisible())
    window.close()


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
        validate_assets=False,
    )
    captured = capsys.readouterr()
    assert result == 0
    assert captured.out.strip() == "PyWatermarkCleaner GUI smoke test: OK"
