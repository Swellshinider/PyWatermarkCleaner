"""Coordinate-safe regions shared by previews and exports."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, inf, isfinite, nextafter


def _validate_frame_dimensions(frame_width: int, frame_height: int) -> None:
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("frame width and height must be positive")


@dataclass(frozen=True, slots=True)
class PixelRegion:
    """A rectangular region expressed in frame pixels."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("region width and height must be positive")

    def clamped(self, frame_width: int, frame_height: int) -> PixelRegion:
        """Return this region's intersection with the frame."""
        _validate_frame_dimensions(frame_width, frame_height)
        left = max(0, self.x)
        top = max(0, self.y)
        right = min(frame_width, self.x + self.width)
        bottom = min(frame_height, self.y + self.height)
        if left >= right or top >= bottom:
            raise ValueError("region does not overlap the frame")
        return PixelRegion(left, top, right - left, bottom - top)

    def to_normalized(self, frame_width: int, frame_height: int) -> NormalizedRegion:
        """Clip and express this region as fractions of the frame dimensions."""
        clipped = self.clamped(frame_width, frame_height)
        return NormalizedRegion(
            clipped.x / frame_width,
            clipped.y / frame_height,
            clipped.width / frame_width,
            clipped.height / frame_height,
        )


@dataclass(frozen=True, slots=True)
class NormalizedRegion:
    """A resolution-independent rectangular region."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not all(isfinite(value) for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("normalized region values must be finite")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("region width and height must be positive")

    def clamped(self) -> NormalizedRegion:
        """Return this region's intersection with the unit square."""
        left = max(0.0, self.x)
        top = max(0.0, self.y)
        right = min(1.0, self.x + self.width)
        bottom = min(1.0, self.y + self.height)
        if left >= right or top >= bottom:
            raise ValueError("region does not overlap the normalized frame")
        return NormalizedRegion(left, top, right - left, bottom - top)

    def to_pixels(self, frame_width: int, frame_height: int) -> PixelRegion:
        """Clip and convert using inclusive origins and exclusive extents."""
        _validate_frame_dimensions(frame_width, frame_height)
        clipped = self.clamped()
        left = floor(nextafter(clipped.x * frame_width, inf))
        top = floor(nextafter(clipped.y * frame_height, inf))
        right = ceil(nextafter((clipped.x + clipped.width) * frame_width, -inf))
        bottom = ceil(nextafter((clipped.y + clipped.height) * frame_height, -inf))
        return PixelRegion(left, top, right - left, bottom - top)

    @classmethod
    def from_cli_pixels(
        cls,
        x: int,
        y: int,
        width: int,
        height: int,
        frame_width: int,
        frame_height: int,
    ) -> NormalizedRegion:
        """Resolve CLI offsets, clip them, and normalize the selected rectangle."""
        _validate_frame_dimensions(frame_width, frame_height)
        resolved_x = frame_width + x if x < 0 else x
        resolved_y = frame_height + y if y < 0 else y
        return PixelRegion(resolved_x, resolved_y, width, height).to_normalized(
            frame_width, frame_height
        )
