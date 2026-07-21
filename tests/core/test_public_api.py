import pywatermarkcleaner.core as core


def test_core_reexports_stable_domain_api() -> None:
    expected = {
        "CancelledError",
        "CancellationToken",
        "ExportRequest",
        "FormatPolicy",
        "InpaintMethod",
        "JobState",
        "PerformanceMode",
        "NormalizedRegion",
        "PixelRegion",
        "PreviewRequest",
        "PreviewResult",
        "ProcessingOptions",
        "ProgressEvent",
        "VideoMetadata",
        "allocate_output_path",
        "create_mask",
        "inpaint_frame",
    }

    assert set(core.__all__) == expected
    assert all(getattr(core, name) is not None for name in expected)
