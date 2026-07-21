"""NumPy/OpenCV primitives for removing a selected watermark region."""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from .geometry import NormalizedRegion, PixelRegion
from .models import InpaintMethod, ProcessingOptions


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
    if (
        not isinstance(frame, np.ndarray)
        or frame.dtype != np.uint8
        or frame.ndim != 3
        or frame.shape[2] != 3
        or frame.size == 0
    ):
        raise ValueError("frame must be a nonempty uint8 BGR NumPy array")

    pixel_region = region.to_pixels(frame.shape[1], frame.shape[0])
    mask = create_mask(frame.shape, pixel_region)
    algorithm = {
        InpaintMethod.TELEA: cv2.INPAINT_TELEA,
        InpaintMethod.NAVIER_STOKES: cv2.INPAINT_NS,
    }[options.method]
    result = cv2.inpaint(frame.copy(), mask, options.radius, algorithm)
    return cast(NDArray[np.uint8], result)
