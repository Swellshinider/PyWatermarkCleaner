from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt

from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.gui.canvas import RepairCanvas


@pytest.fixture
def canvas(qtbot) -> RepairCanvas:
    widget = RepairCanvas()
    widget.resize(640, 400)
    source = np.zeros((100, 200, 3), dtype=np.uint8)
    source[:, :, 0] = 255
    cleaned = np.zeros_like(source)
    cleaned[:, :, 1] = 255
    widget.set_frames(source, cleaned, source_size=(200, 100))
    widget.set_region(NormalizedRegion(0.25, 0.25, 0.5, 0.5))
    qtbot.addWidget(widget)
    widget.show()
    widget.setFocus()
    return widget


def test_canvas_frame_mapping_and_focus_accessibility(canvas: RepairCanvas) -> None:
    frame = canvas.displayed_frame_rect()
    assert frame.width() == pytest.approx(640)
    assert frame.height() == pytest.approx(320)
    assert frame.top() == pytest.approx(40)
    assert canvas.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert canvas.accessibleName() == "Repair preview and region editor"
    mapped = canvas.region_widget_rect()
    assert mapped.left() == pytest.approx(160)
    assert mapped.top() == pytest.approx(120)


def test_canvas_draw_move_clamp_escape_and_nudge(qtbot, canvas: RepairCanvas) -> None:
    frame = canvas.displayed_frame_rect()
    canvas.set_region(None)
    with qtbot.waitSignal(canvas.region_changed, timeout=500):
        qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(64, 72))
        qtbot.mouseMove(canvas, QPoint(192, 136))
        qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(192, 136))
    assert canvas.region is not None
    assert canvas.region.to_pixels(200, 100).width >= 2

    original = canvas.region
    center = canvas.region_widget_rect().center().toPoint()
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=center)
    qtbot.mouseMove(canvas, QPoint(int(frame.right() + 100), center.y()))
    qtbot.keyClick(canvas, Qt.Key.Key_Escape)
    assert canvas.region == original

    before = canvas.region.to_pixels(200, 100)
    qtbot.keyClick(canvas, Qt.Key.Key_Right)
    after_one = canvas.region.to_pixels(200, 100)
    assert after_one.x == before.x + 1
    qtbot.keyClick(canvas, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    after_ten = canvas.region.to_pixels(200, 100)
    assert after_ten.y == before.y + 10


@pytest.mark.parametrize("handle", ["nw", "n", "ne", "e", "se", "s", "sw", "w"])
def test_canvas_all_eight_handles_resize_and_clamp(
    qtbot, canvas: RepairCanvas, handle: str
) -> None:
    before = canvas.region
    point = canvas.handle_points()[handle].toPoint()
    delta = {
        "nw": QPoint(-500, -500),
        "n": QPoint(0, -500),
        "ne": QPoint(500, -500),
        "e": QPoint(500, 0),
        "se": QPoint(500, 500),
        "s": QPoint(0, 500),
        "sw": QPoint(-500, 500),
        "w": QPoint(-500, 0),
    }[handle]
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=point)
    qtbot.mouseMove(canvas, point + delta)
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=point + delta)
    assert canvas.region != before
    pixels = canvas.region.to_pixels(200, 100)
    assert pixels.x >= 0 and pixels.y >= 0
    assert pixels.x + pixels.width <= 200
    assert pixels.y + pixels.height <= 100
    assert pixels.width >= 2 and pixels.height >= 2


def test_aperture_paints_cleaned_and_space_reveals_original(qtbot, canvas: RepairCanvas) -> None:
    aperture = canvas.region_widget_rect().center().toPoint()
    outside = QPoint(20, 200)
    pixmap = canvas.grab()
    assert pixmap.toImage().pixelColor(aperture).green() > 200
    assert pixmap.toImage().pixelColor(outside).blue() > 200

    qtbot.keyPress(canvas, Qt.Key.Key_Space)
    revealed = canvas.grab().toImage().pixelColor(aperture)
    assert revealed.blue() > 200 and revealed.green() < 50
    qtbot.keyRelease(canvas, Qt.Key.Key_Space)
