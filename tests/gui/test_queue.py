from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import Qt

from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.models import JobState, VideoMetadata
from pywatermarkcleaner.gui.queue import ProbeController, QueueModel, QueueRole


def metadata(path: Path, width: int = 1920, height: int = 1080) -> VideoMetadata:
    return VideoMetadata(path.resolve(), width, height, 30.0, 300, 10.0, "mp4")


def test_queue_model_exposes_state_roles_and_ignores_duplicate(qtbot, tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.touch()
    model = QueueModel()
    with qtbot.waitSignal(model.status_message, timeout=500) as duplicate:
        assert model.add_metadata(metadata(source))
        assert not model.add_metadata(metadata(source.parent / "." / source.name))

    index = model.index(0)
    assert model.rowCount() == 1
    assert model.data(index, Qt.ItemDataRole.DisplayRole) == "clip.mp4"
    assert model.data(index, QueueRole.STATE) == JobState.NEEDS_REGION
    assert model.data(index, QueueRole.STATE_LABEL) == "Needs region"
    assert "already" in duplicate.args[0].lower()

    region = NormalizedRegion(0.1, 0.2, 0.3, 0.2)
    model.set_region(0, region)
    model.update_job(0, state=JobState.PROCESSING, progress=42)
    assert model.data(index, QueueRole.REGION) == region
    assert model.data(index, QueueRole.PROGRESS) == 42
    assert model.data(index, QueueRole.STATE_LABEL) == "Processing"


def test_probe_controller_adds_valid_and_reports_invalid_off_thread(qtbot, tmp_path: Path) -> None:
    good = tmp_path / "good.mp4"
    bad = tmp_path / "bad.txt"
    good.touch()
    bad.touch()

    class Reader:
        def probe(self, path: Path) -> VideoMetadata:
            if path == bad.resolve():
                raise ValueError("unsupported codec; transcode the file")
            return metadata(path)

    model = QueueModel()
    controller = ProbeController(model, reader=Reader())
    added: list[Path] = []
    messages: list[str] = []
    controller.file_added.connect(added.append)
    controller.message.connect(messages.append)
    with qtbot.waitSignals([controller.file_added, controller.message], timeout=2000, order="none"):
        controller.add_paths([good, bad])

    assert model.rowCount() == 1
    assert added == [good.resolve()]
    assert "transcode" in messages[0]
    controller.close()


def test_probe_results_are_ignored_after_close(qtbot, tmp_path: Path) -> None:
    source = tmp_path / "slow.mp4"
    source.touch()
    release = Event()

    class Reader:
        def probe(self, path: Path) -> VideoMetadata:
            release.wait(1)
            return metadata(path)

    model = QueueModel()
    controller = ProbeController(model, reader=Reader())
    controller.add_paths([source])
    controller.close()
    release.set()
    qtbot.wait(100)
    assert model.rowCount() == 0


def test_selection_data_retains_timeline_region_output_and_error(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.touch()
    model = QueueModel()
    model.add_metadata(metadata(source))
    region = NormalizedRegion(0.2, 0.3, 0.1, 0.1)
    output = tmp_path / "clip_cleaned.mp4"
    model.set_region(0, region)
    model.set_timeline_position(0, 1250)
    model.update_job(0, state=JobState.FAILED, output_path=output, error="disk full")
    item = model.item(0)
    assert (item.timeline_position_ms, item.region, item.output_path, item.error) == (
        1250,
        region,
        output,
        "disk full",
    )


def test_region_must_cover_two_by_two_source_pixels(tmp_path: Path) -> None:
    source = tmp_path / "tiny.mp4"
    source.touch()
    model = QueueModel()
    model.add_metadata(metadata(source, width=50, height=50))

    assert not model.set_region(0, NormalizedRegion(0.98, 0.98, 0.01, 0.01))
    assert model.item(0).region is None
    assert model.item(0).state == JobState.NEEDS_REGION
    assert not model.all_ready()

    assert model.set_region(0, NormalizedRegion(0.9, 0.9, 0.04, 0.04))
    assert model.item(0).state == JobState.READY
    assert model.all_ready()


@pytest.mark.parametrize("active_state", [JobState.QUEUED, JobState.PROCESSING])
def test_invalid_region_cannot_replace_active_job_state(
    tmp_path: Path, active_state: JobState
) -> None:
    source = tmp_path / f"{active_state.value}.mp4"
    source.touch()
    model = QueueModel()
    model.add_metadata(metadata(source, width=50, height=50))
    model.set_region(0, NormalizedRegion(0.9, 0.9, 0.04, 0.04))
    model.update_job(0, state=active_state)

    assert not model.set_region(0, NormalizedRegion(0.98, 0.98, 0.01, 0.01))
    assert model.item(0).state == active_state


def test_failed_row_exposes_plain_accessible_remedy(tmp_path: Path) -> None:
    source = tmp_path / "broken.mp4"
    source.touch()
    model = QueueModel()
    model.add_metadata(metadata(source))
    model.update_job(
        0,
        state=JobState.FAILED,
        error="Disk is full. Choose another output folder and retry.",
    )

    accessible = model.data(model.index(0), Qt.ItemDataRole.AccessibleTextRole)
    assert "Failed" in accessible
    assert "Choose another output folder" in accessible
