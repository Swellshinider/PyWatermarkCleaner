import math

import pytest

from pywatermarkcleaner.core.geometry import NormalizedRegion, PixelRegion


@pytest.mark.parametrize(
    ("width", "height"),
    [(0, 1), (1, 0), (-1, 1), (1, -1)],
)
def test_pixel_region_requires_positive_size(width: int, height: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        PixelRegion(0, 0, width, height)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_normalized_region_rejects_nonfinite_values(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        NormalizedRegion(value, 0.0, 0.5, 0.5)


@pytest.mark.parametrize(
    ("width", "height"),
    [(0.0, 0.5), (0.5, 0.0), (-0.5, 0.5), (0.5, -0.5)],
)
def test_normalized_region_requires_positive_size(width: float, height: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        NormalizedRegion(0.0, 0.0, width, height)


def test_pixel_region_clamps_to_frame_without_mutating_source() -> None:
    source = PixelRegion(-5, 3, 10, 8)

    result = source.clamped(20, 10)

    assert result == PixelRegion(0, 3, 5, 7)
    assert source == PixelRegion(-5, 3, 10, 8)


@pytest.mark.parametrize(
    "region",
    [PixelRegion(-10, 0, 5, 5), PixelRegion(10, 0, 5, 5), PixelRegion(0, 10, 5, 5)],
)
def test_pixel_region_rejects_no_frame_overlap(region: PixelRegion) -> None:
    with pytest.raises(ValueError, match="overlap"):
        region.clamped(10, 10)


def test_pixel_region_converts_to_clipped_normalized_coordinates() -> None:
    source = PixelRegion(-5, 3, 10, 8)

    result = source.to_normalized(20, 10)

    assert result == NormalizedRegion(0.0, 0.3, 0.25, 0.7)
    assert source == PixelRegion(-5, 3, 10, 8)


def test_normalized_region_clamps_to_unit_bounds_without_mutating_source() -> None:
    source = NormalizedRegion(-0.1, 0.25, 0.5, 1.0)

    result = source.clamped()

    assert result == NormalizedRegion(0.0, 0.25, 0.4, 0.75)
    assert source == NormalizedRegion(-0.1, 0.25, 0.5, 1.0)


def test_normalized_region_rejects_no_unit_square_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        NormalizedRegion(1.0, 0.0, 0.5, 0.5).clamped()


def test_normalized_to_pixels_uses_floor_for_origin_and_ceil_for_extent() -> None:
    result = NormalizedRegion(0.1, 0.2, 0.2, 0.3).to_pixels(101, 99)

    assert result == PixelRegion(10, 19, 21, 31)


def test_normalized_to_pixels_uses_literal_floor_immediately_below_boundary() -> None:
    x = math.nextafter(0.5, -math.inf)

    result = NormalizedRegion(x, 0.0, 0.25, 1.0).to_pixels(2, 1)

    assert result == PixelRegion(0, 0, 2, 1)


def test_normalized_to_pixels_uses_literal_ceil_immediately_above_boundary() -> None:
    width = math.nextafter(0.2, math.inf)

    result = NormalizedRegion(0.0, 0.0, width, 1.0).to_pixels(100, 1)

    assert result == PixelRegion(0, 0, 21, 1)


def test_normalized_to_pixels_maps_tiny_positive_extent_to_one_pixel() -> None:
    width = math.nextafter(0.0, math.inf)

    result = NormalizedRegion(0.0, 0.0, width, 1.0).to_pixels(1, 1)

    assert result == PixelRegion(0, 0, 1, 1)


def test_negative_cli_coordinates_are_resolved_from_right_and_bottom() -> None:
    result = NormalizedRegion.from_cli_pixels(-30, -20, 20, 10, 100, 50)

    assert result == NormalizedRegion(0.7, 0.6, 0.2, 0.2)


def test_cli_pixel_region_is_clipped_before_normalization() -> None:
    result = NormalizedRegion.from_cli_pixels(-10, -5, 20, 10, 100, 50)

    assert result == NormalizedRegion(0.9, 0.9, 0.1, 0.1)


def test_normalized_copy_scales_to_a_mixed_resolution() -> None:
    source = NormalizedRegion.from_cli_pixels(10, 20, 30, 40, 100, 200)

    result = source.to_pixels(200, 100)

    assert result == PixelRegion(20, 10, 60, 20)


def test_pixel_normalized_round_trip_preserves_integer_boundaries() -> None:
    source = PixelRegion(10, 20, 30, 40)

    result = source.to_normalized(100, 200).to_pixels(100, 200)

    assert result == source
