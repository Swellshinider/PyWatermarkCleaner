from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from pywatermarkcleaner.core.cancellation import CancellationToken, CancelledError
from pywatermarkcleaner.core.export import VideoExporter
from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.media import OpenCVMediaReader
from pywatermarkcleaner.core.models import ExportRequest, JobState, ProcessingOptions


def _request(source: Path, output: Path) -> ExportRequest:
    return ExportRequest(
        input_path=source,
        output_path=output,
        region=NormalizedRegion(0.1, 0.1, 0.25, 0.25),
        options=ProcessingOptions(),
    )


def _ffmpeg(
    executable: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        [str(executable), "-hide_banner", "-loglevel", "error", *arguments],
        capture_output=True,
        check=False,
    )
    if check:
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed


@pytest.mark.parametrize("with_audio", [False, True], ids=["video-only", "audio"])
def test_real_export_preserves_dimensions_duration_and_metadata(
    synthetic_video_factory: Callable[..., Path],
    ffmpeg_executable: Path,
    tmp_path: Path,
    with_audio: bool,
) -> None:
    source = synthetic_video_factory(name=f"source-{with_audio}.mp4", with_audio=with_audio)
    output = tmp_path / f"cleaned-{with_audio}.mp4"
    source_metadata = OpenCVMediaReader().probe(source)

    result = VideoExporter().export(_request(source, output), CancellationToken())

    output_metadata = OpenCVMediaReader().probe(result)
    assert (output_metadata.width, output_metadata.height) == (
        source_metadata.width,
        source_metadata.height,
    )
    assert output_metadata.frame_count == source_metadata.frame_count
    assert abs(output_metadata.duration_seconds - source_metadata.duration_seconds) <= 0.15
    metadata = _ffmpeg(
        ffmpeg_executable,
        "-i",
        str(result),
        "-map_metadata",
        "0",
        "-f",
        "ffmetadata",
        "pipe:1",
    ).stdout.decode(errors="replace")
    assert "title=PyWatermarkCleaner synthetic fixture" in metadata
    audio_probe = _ffmpeg(
        ffmpeg_executable,
        "-i",
        str(result),
        "-map",
        "0:a:0",
        "-f",
        "null",
        "-",
        check=False,
    )
    assert (audio_probe.returncode == 0) is with_audio


def test_real_export_cancellation_removes_partial_output(
    synthetic_video_factory: Callable[..., Path], tmp_path: Path
) -> None:
    source = synthetic_video_factory(name="cancel-source.mp4", with_audio=True, duration=2.0)
    output = tmp_path / "cancel-cleaned.mp4"
    token = CancellationToken()

    def cancel_after_first_frame(event) -> None:  # type: ignore[no-untyped-def]
        if event.state == JobState.PROCESSING and event.frames_done == 1:
            token.cancel()

    with pytest.raises(CancelledError):
        VideoExporter().export(_request(source, output), token, cancel_after_first_frame)

    assert not output.exists()
    assert not (tmp_path / "cancel-cleaned.partial.mp4").exists()


def test_legacy_cli_and_web_real_smoke_paths(
    synthetic_video_factory: Callable[..., Path], tmp_path: Path
) -> None:
    source = synthetic_video_factory(name="entrypoint-source.mp4", with_audio=False)
    output_directory = tmp_path / "cli-output"
    repository = Path(__file__).resolve().parents[2]
    cli = subprocess.run(
        [
            sys.executable,
            str(repository / "main.py"),
            "-i",
            str(source),
            "--x",
            "4",
            "--y",
            "4",
            "--width",
            "16",
            "--height",
            "12",
            "--output-dir",
            str(output_directory),
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli.returncode == 0, cli.stderr
    assert (output_directory / "entrypoint-source_cleaned.mp4").is_file()
    assert "Completed" in cli.stdout

    gui = subprocess.run(
        [sys.executable, "-m", "pywatermarkcleaner.web", "--smoke-test"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    assert gui.returncode == 0, gui.stderr
