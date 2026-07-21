from collections.abc import Callable
from typing import cast

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from pywatermarkcleaner.core.geometry import NormalizedRegion, PixelRegion
from pywatermarkcleaner.core.inpainting import create_mask, inpaint_frame
from pywatermarkcleaner.core.models import InpaintMethod, ProcessingOptions


def test_create_mask_sets_exact_clipped_pixel_area() -> None:
    mask = create_mask((10, 12, 3), PixelRegion(-2, 8, 5, 5))

    assert mask.shape == (10, 12)
    assert mask.dtype == np.uint8
    assert np.count_nonzero(mask) == 6
    assert mask.sum() == 6 * 255
    assert np.all(mask[8:10, 0:3] == 255)


@pytest.mark.parametrize(
    ("method", "expected_flag"),
    [
        (InpaintMethod.TELEA, cv2.INPAINT_TELEA),
        (InpaintMethod.NAVIER_STOKES, cv2.INPAINT_NS),
    ],
)
def test_inpaint_frame_maps_algorithm_and_preserves_input(
    method: InpaintMethod,
    expected_flag: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    frame[2:6, 2:6] = 255
    original = frame.copy()
    calls: list[tuple[NDArray[np.uint8], NDArray[np.uint8], float, int]] = []
    opencv_inpaint = cast(Callable[..., NDArray[np.uint8]], cv2.inpaint)

    def recording_inpaint(
        source: NDArray[np.uint8], mask: NDArray[np.uint8], radius: float, flag: int
    ) -> NDArray[np.uint8]:
        calls.append((source, mask, radius, flag))
        return opencv_inpaint(source, mask, radius, flag)

    monkeypatch.setattr(cv2, "inpaint", recording_inpaint)

    result = inpaint_frame(
        frame,
        NormalizedRegion(0.25, 0.25, 0.5, 0.5),
        ProcessingOptions(method, 4),
    )

    assert result.shape == frame.shape
    assert result.dtype == np.uint8
    assert np.array_equal(frame, original)
    assert len(calls) == 1
    called_frame, called_mask, called_radius, called_flag = calls[0]
    assert called_frame is not frame
    assert np.count_nonzero(called_mask) == 16
    assert called_radius == 4
    assert called_flag == expected_flag


@pytest.mark.parametrize(
    "frame",
    [
        np.zeros((2, 2, 3), dtype=np.float32),
        np.zeros((2, 2), dtype=np.uint8),
        np.zeros((2, 2, 4), dtype=np.uint8),
        np.zeros((0, 2, 3), dtype=np.uint8),
    ],
)
def test_inpaint_frame_rejects_invalid_dtype_shape_or_empty_frame(
    frame: NDArray[np.generic],
) -> None:
    with pytest.raises(ValueError, match="uint8 BGR"):
        inpaint_frame(
            cast(NDArray[np.uint8], frame),
            NormalizedRegion(0.0, 0.0, 0.5, 0.5),
            ProcessingOptions(),
        )
