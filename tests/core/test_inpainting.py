from collections.abc import Callable
from typing import cast

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from pywatermarkcleaner.core.geometry import NormalizedRegion, PixelRegion
from pywatermarkcleaner.core.inpainting import (
    ReusingInpainter,
    create_mask,
    inpaint_frame,
    prepare_inpainting,
)
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


def _noisy_frame(seed: int) -> NDArray[np.uint8]:
    return np.random.default_rng(seed).integers(0, 255, (40, 60, 3), dtype=np.uint8)


def test_plan_apply_in_place_writes_the_callers_frame_only_when_asked() -> None:
    plan = prepare_inpainting(
        (40, 60, 3), NormalizedRegion(0.4, 0.4, 0.2, 0.2), ProcessingOptions()
    )
    frame = _noisy_frame(0)
    original = frame.copy()

    assert plan.apply(frame) is not frame
    assert np.array_equal(frame, original)
    assert plan.apply(frame, in_place=True) is frame
    assert not np.array_equal(frame, original)


def test_reusing_inpainter_reuses_fill_while_surroundings_hold_and_refills_when_they_change() -> (
    None
):
    plan = prepare_inpainting(
        (40, 60, 3), NormalizedRegion(0.4, 0.4, 0.2, 0.2), ProcessingOptions()
    )
    reusing = ReusingInpainter(plan)
    first = _noisy_frame(1)
    reusing.apply(first)

    # Same surroundings, different content under the mask: the keyframe fill is reused.
    same_surroundings = first.copy()
    plan.view(same_surroundings)[plan.mask > 0] = 0
    assert np.array_equal(reusing.apply(same_surroundings), plan.apply(first))

    # Different surroundings: falls back to a full inpaint of that frame.
    other = _noisy_frame(2)
    assert np.array_equal(reusing.apply(other), plan.apply(other))


def test_reusing_inpainter_never_reuses_when_the_mask_has_no_surroundings() -> None:
    plan = prepare_inpainting(
        (40, 60, 3), NormalizedRegion(0.0, 0.0, 1.0, 1.0), ProcessingOptions()
    )
    reusing = ReusingInpainter(plan)
    first, second = _noisy_frame(3), _noisy_frame(4)
    reusing.apply(first)

    assert np.array_equal(reusing.apply(second), plan.apply(second))
