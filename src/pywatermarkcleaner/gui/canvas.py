"""Interactive fitted-frame repair aperture canvas."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from pywatermarkcleaner.core.geometry import NormalizedRegion, PixelRegion


class RepairCanvas(QWidget):
    region_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Repair preview and region editor")
        self.setAccessibleDescription(
            "Draw, move, resize, or nudge the rectangular watermark region."
        )
        self.setMouseTracking(True)
        self.setMinimumSize(320, 220)
        self._source: QImage | None = None
        self._cleaned: QImage | None = None
        self._source_size = (1, 1)
        self.region: NormalizedRegion | None = None
        self._reveal_original = False
        self._drag_mode: str | None = None
        self._drag_origin = QPointF()
        self._drag_initial: NormalizedRegion | None = None

    @staticmethod
    def _to_image(frame: NDArray[np.uint8]) -> QImage:
        contiguous = np.ascontiguousarray(frame)
        height, width = contiguous.shape[:2]
        return QImage(
            contiguous.data,
            width,
            height,
            int(contiguous.strides[0]),
            QImage.Format.Format_BGR888,
        ).copy()

    def set_frames(
        self,
        source: NDArray[np.uint8],
        cleaned: NDArray[np.uint8] | None = None,
        *,
        source_size: tuple[int, int] | None = None,
    ) -> None:
        self._source = self._to_image(source)
        self._cleaned = self._to_image(cleaned) if cleaned is not None else None
        self._source_size = source_size or (source.shape[1], source.shape[0])
        self.update()

    def set_region(self, region: NormalizedRegion | None) -> None:
        if region is None:
            self.region = None
        elif (
            region.x >= 0
            and region.y >= 0
            and region.x + region.width <= 1
            and region.y + region.height <= 1
        ):
            self.region = region
        else:
            self.region = region.clamped()
        self.update()

    def displayed_frame_rect(self) -> QRectF:
        if self._source is None:
            return QRectF(self.rect())
        source_ratio = self._source.width() / self._source.height()
        available = QRectF(self.rect())
        if available.width() / max(1.0, available.height()) > source_ratio:
            height = available.height()
            width = height * source_ratio
        else:
            width = available.width()
            height = width / source_ratio
        return QRectF(
            available.center().x() - width / 2,
            available.center().y() - height / 2,
            width,
            height,
        )

    def region_widget_rect(self) -> QRectF:
        if self.region is None:
            return QRectF()
        frame = self.displayed_frame_rect()
        return QRectF(
            frame.left() + self.region.x * frame.width(),
            frame.top() + self.region.y * frame.height(),
            self.region.width * frame.width(),
            self.region.height * frame.height(),
        )

    def handle_points(self) -> Mapping[str, QPointF]:
        rect = self.region_widget_rect()
        return {
            "nw": rect.topLeft(),
            "n": QPointF(rect.center().x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), rect.center().y()),
            "se": rect.bottomRight(),
            "s": QPointF(rect.center().x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), rect.center().y()),
        }

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101A24"))
        frame = self.displayed_frame_rect()
        if self._source is None:
            painter.setPen(QColor("#96A9B3"))
            painter.drawText(
                frame,
                Qt.AlignmentFlag.AlignCenter,
                "Drop videos here\nor choose files",
            )
            return
        painter.drawImage(frame, self._source)
        aperture = self.region_widget_rect()
        if self.region is not None and self._cleaned is not None and not self._reveal_original:
            painter.save()
            painter.setClipRect(aperture)
            painter.drawImage(frame, self._cleaned)
            painter.restore()
        if self.region is not None:
            painter.setPen(QPen(QColor("#59B7C8"), 2))
            painter.drawRect(aperture)
            painter.setBrush(QColor("#E7F0F4"))
            for point in self.handle_points().values():
                painter.drawRect(QRectF(point.x() - 4, point.y() - 4, 8, 8))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#59B7C8"), 1, Qt.PenStyle.DotLine))
            painter.drawRect(QRectF(self.rect()).adjusted(2, 2, -3, -3))

    def _handle_at(self, point: QPointF) -> str | None:
        for name, position in self.handle_points().items():
            if abs(point.x() - position.x()) <= 8 and abs(point.y() - position.y()) <= 8:
                return name
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._source is None:
            return
        self.setFocus()
        point = event.position()
        self._drag_initial = self.region
        self._drag_origin = point
        handle = self._handle_at(point) if self.region is not None else None
        if handle is not None:
            self._drag_mode = handle
        elif self.region is not None and self.region_widget_rect().contains(point):
            self._drag_mode = "move"
        else:
            self._drag_mode = "draw"
            self.region = self._region_from_points(point, point)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_mode is None:
            return
        if self._drag_mode == "draw":
            self.region = self._region_from_points(self._drag_origin, event.position())
        elif self._drag_mode == "move" and self._drag_initial is not None:
            frame = self.displayed_frame_rect()
            dx = (event.position().x() - self._drag_origin.x()) / frame.width()
            dy = (event.position().y() - self._drag_origin.y()) / frame.height()
            initial = self._drag_initial
            x = min(max(0.0, initial.x + dx), 1.0 - initial.width)
            y = min(max(0.0, initial.y + dy), 1.0 - initial.height)
            self.region = NormalizedRegion(x, y, initial.width, initial.height)
        elif self._drag_initial is not None:
            self.region = self._resized_region(self._drag_mode, event.position())
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_mode is not None:
            self.mouseMoveEvent(event)
            self._drag_mode = None
            if self.region is not None:
                self.region_changed.emit(self.region)

    def _region_from_points(self, first: QPointF, second: QPointF) -> NormalizedRegion:
        frame = self.displayed_frame_rect()
        width, height = self._source_size
        left = min(max(frame.left(), min(first.x(), second.x())), frame.right())
        right = min(max(frame.left(), max(first.x(), second.x())), frame.right())
        top = min(max(frame.top(), min(first.y(), second.y())), frame.bottom())
        bottom = min(max(frame.top(), max(first.y(), second.y())), frame.bottom())
        min_w = 2 / width
        min_h = 2 / height
        x = (left - frame.left()) / frame.width()
        y = (top - frame.top()) / frame.height()
        normalized_width = max(min_w, (right - left) / frame.width())
        normalized_height = max(min_h, (bottom - top) / frame.height())
        return NormalizedRegion(
            min(x, 1 - normalized_width),
            min(y, 1 - normalized_height),
            min(normalized_width, 1.0),
            min(normalized_height, 1.0),
        )

    def _resized_region(self, handle: str, point: QPointF) -> NormalizedRegion:
        assert self._drag_initial is not None
        initial = self._drag_initial.to_pixels(*self._source_size)
        frame = self.displayed_frame_rect()
        px = round((point.x() - frame.left()) / frame.width() * self._source_size[0])
        py = round((point.y() - frame.top()) / frame.height() * self._source_size[1])
        px = min(max(0, px), self._source_size[0])
        py = min(max(0, py), self._source_size[1])
        left, top = initial.x, initial.y
        right, bottom = initial.x + initial.width, initial.y + initial.height
        if "w" in handle:
            left = min(px, right - 2)
        if "e" in handle:
            right = max(px, left + 2)
        if "n" in handle:
            top = min(py, bottom - 2)
        if "s" in handle:
            bottom = max(py, top + 2)
        left = max(0, left)
        top = max(0, top)
        right = min(self._source_size[0], right)
        bottom = min(self._source_size[1], bottom)
        return PixelRegion(left, top, max(2, right - left), max(2, bottom - top)).to_normalized(
            *self._source_size
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space:
            self._reveal_original = True
            self.update()
            return
        if event.key() == Qt.Key.Key_Escape and self._drag_mode is not None:
            self.region = self._drag_initial
            self._drag_mode = None
            self.update()
            return
        directions: dict[int, tuple[int, int]] = {
            int(Qt.Key.Key_Left): (-1, 0),
            int(Qt.Key.Key_Right): (1, 0),
            int(Qt.Key.Key_Up): (0, -1),
            int(Qt.Key.Key_Down): (0, 1),
        }
        if self.region is not None and event.key() in directions:
            multiplier = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx, dy = directions[event.key()]
            pixels = self.region.to_pixels(*self._source_size)
            x = min(max(0, pixels.x + dx * multiplier), self._source_size[0] - pixels.width)
            y = min(max(0, pixels.y + dy * multiplier), self._source_size[1] - pixels.height)
            self.region = PixelRegion(x, y, pixels.width, pixels.height).to_normalized(
                *self._source_size
            )
            self.region_changed.emit(self.region)
            self.update()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space:
            self._reveal_original = False
            self.update()
            return
        super().keyReleaseEvent(event)
