"""NumPy/OpenCV primitives for removing a selected watermark region."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from .geometry import NormalizedRegion, PixelRegion
from .models import InpaintMethod, ProcessingOptions


@dataclass(frozen=True, slots=True)
class InpaintPlan:
    """Reusable cropped mask and geometry for equal-sized video frames."""

    crop: PixelRegion
    mask: NDArray[np.uint8]
    options: ProcessingOptions

    def apply(self, frame: NDArray[np.uint8]) -> NDArray[np.uint8]:
        _validate_frame(frame)
        right = self.crop.x + self.crop.width
        bottom = self.crop.y + self.crop.height
        if right > frame.shape[1] or bottom > frame.shape[0]:
            raise ValueError("frame dimensions do not match the inpainting plan")
        source = frame[self.crop.y : bottom, self.crop.x : right]
        algorithm = {
            InpaintMethod.TELEA: cv2.INPAINT_TELEA,
            InpaintMethod.NAVIER_STOKES: cv2.INPAINT_NS,
        }[self.options.method]
        cleaned_crop = cv2.inpaint(source.copy(), self.mask, self.options.radius, algorithm)
        result = frame.copy()
        result[self.crop.y : bottom, self.crop.x : right] = cleaned_crop
        return result


def _validate_frame(frame: NDArray[np.uint8]) -> None:
    if (
        not isinstance(frame, np.ndarray)
        or frame.dtype != np.uint8
        or frame.ndim != 3
        or frame.shape[2] != 3
        or frame.size == 0
    ):
        raise ValueError("frame must be a nonempty uint8 BGR NumPy array")


def prepare_inpainting(
    frame_shape: tuple[int, ...],
    region: NormalizedRegion,
    options: ProcessingOptions,
) -> InpaintPlan:
    """Prepare a mask limited to the selection plus a ``2 * radius`` margin."""
    if len(frame_shape) < 2:
        raise ValueError("frame shape must contain height and width")
    frame_height, frame_width = frame_shape[:2]
    pixel = region.to_pixels(frame_width, frame_height)
    margin = 2 * options.radius
    left = max(0, pixel.x - margin)
    top = max(0, pixel.y - margin)
    right = min(frame_width, pixel.x + pixel.width + margin)
    bottom = min(frame_height, pixel.y + pixel.height + margin)
    crop = PixelRegion(left, top, right - left, bottom - top)
    local = PixelRegion(pixel.x - left, pixel.y - top, pixel.width, pixel.height)
    return InpaintPlan(crop, create_mask((crop.height, crop.width), local), options)


def create_mask(frame_shape: tuple[int, ...], region: PixelRegion) -> NDArray[np.uint8]:
    """Create an OpenCV mask for the region intersecting the frame."""
    if len(frame_shape) < 2:
        raise ValueError("frame shape must contain height and width")
    frame_height, frame_width = frame_shape[:2]
    clipped = region.clamped(frame_width, frame_height)
    mask = np.zeros((frame_height, frame_width), dtype=np.uint8)
    mask[clipped.y : clipped.y + clipped.height, clipped.x : clipped.x + clipped.width] = 255
    return mask


def inpaint_frame(
    frame: NDArray[np.uint8],
    region: NormalizedRegion,
    options: ProcessingOptions,
) -> NDArray[np.uint8]:
    """Return a cleaned copy of one uint8 BGR frame."""
    _validate_frame(frame)
    return prepare_inpainting(frame.shape, region, options).apply(frame)
