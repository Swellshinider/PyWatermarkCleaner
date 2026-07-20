from __future__ import annotations

import os
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import pywatermarkcleaner.core.export as export_module
from pywatermarkcleaner.core.cancellation import CancellationToken, CancelledError
from pywatermarkcleaner.core.exceptions import ExportError, FFmpegNotFoundError
from pywatermarkcleaner.core.export import (
    VideoExporter,
    profile_for_suffix,
    resolve_export_path,
    resolve_ffmpeg_executable,
)
from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.media import OpenCVMediaReader
from pywatermarkcleaner.core.models import (
    ExportRequest,
    JobState,
    ProcessingOptions,
    VideoMetadata,
)


@pytest.mark.parametrize(
    ("suffix", "extension", "expected_args"),
    [
        (".mp4", "mp4", ("-c:v", "libx264", "-crf", "18", "-preset", "medium")),
        ("MOV", "mov", ("-c:v", "libx264", "-crf", "18", "-preset", "medium")),
        ("m4v", "m4v", ("-c:v", "libx264", "-crf", "18", "-preset", "medium")),
        ("mkv", "mkv", ("-c:v", "libx264", "-crf", "18", "-preset", "medium")),
        ("avi", "avi", ("-c:v", "mpeg4", "-q:v", "3")),
        ("webm", "webm", ("-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0")),
    ],
)
def test_profiles_map_supported_containers(
    suffix: str, extension: str, expected_args: tuple[str, ...]
) -> None:
    profile = profile_for_suffix(suffix)

    assert profile.extension == extension
    assert profile.video_args == expected_args
    assert profile.notice is None


def test_unknown_profile_falls_back_to_mp4_with_visible_notice(tmp_path: Path) -> None:
    profile = profile_for_suffix("wmv")
    output = resolve_export_path(Path("capture.wmv"), tmp_path)

    assert profile.extension == "mp4"
    assert "wmv" in (profile.notice or "").lower()
    assert "mp4" in (profile.notice or "").lower()
    assert output == tmp_path / "capture_cleaned.mp4"


def test_h264_uses_compatible_pixel_format_without_changing_dimensions() -> None:
    profile = profile_for_suffix("mp4")

    assert profile.pixel_format(1920, 1080) == "yuv420p"
    assert profile.pixel_format(1919, 1079) == "yuv444p"


def test_resolve_ffmpeg_prefers_valid_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "custom-ffmpeg.exe"
    override.touch()
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", str(override))
    monkeypatch.setattr(
        "imageio_ffmpeg.get_ffmpeg_exe",
        lambda: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )

    assert resolve_ffmpeg_executable() == override


def test_resolve_ffmpeg_falls_back_and_reports_actionable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fallback = tmp_path / "ffmpeg"
    fallback.touch()
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", str(tmp_path / "missing"))
    monkeypatch.setattr("imageio_ffmpeg.get_ffmpeg_exe", lambda: str(fallback))
    assert resolve_ffmpeg_executable() == fallback

    fallback.unlink()
    with pytest.raises(FFmpegNotFoundError, match="imageio-ffmpeg"):
        resolve_ffmpeg_executable()


class StaticMetadataReader:
    def __init__(self, *, width: int = 4, height: int = 2, frames: int = 3) -> None:
        self.metadata = VideoMetadata(
            Path("source.mp4"), width, height, 2.0, frames, frames / 2.0, "mp4"
        )

    def probe(self, path: Path) -> VideoMetadata:
        return VideoMetadata(
            path,
            self.metadata.width,
            self.metadata.height,
            self.metadata.fps,
            self.metadata.frame_count,
            self.metadata.duration_seconds,
            path.suffix.lstrip("."),
        )


class RecordingStdin:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> int:
        self.data.extend(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, command: list[str], *, exit_code: int = 0, stderr: bytes = b"") -> None:
        self.command = command
        self.stdin = RecordingStdin()
        self.stderr = BytesIO(stderr)
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False
        self.partial = Path(command[-1])
        self.partial.write_bytes(b"partial")

    def wait(self, timeout: float | None = None) -> int:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class ProcessFactory:
    def __init__(self, *, exit_code: int = 0, stderr: bytes = b"") -> None:
        self.exit_code = exit_code
        self.stderr = stderr
        self.processes: list[FakeProcess] = []

    def __call__(self, command: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(command, exit_code=self.exit_code, stderr=self.stderr)
        self.processes.append(process)
        return process


def export_request(
    source: Path, output: Path, preview_seconds: float | None = None
) -> ExportRequest:
    return ExportRequest(
        source,
        output,
        NormalizedRegion(0.0, 0.0, 0.5, 0.5),
        ProcessingOptions(),
        preview_seconds,
    )


def make_exporter(
    process_factory: ProcessFactory,
    frames: list[np.ndarray],
    *,
    reader: StaticMetadataReader | None = None,
    inpaint_operation: Any = lambda frame, region, options: frame,
) -> VideoExporter:
    return VideoExporter(
        media_reader=reader or StaticMetadataReader(frames=len(frames)),
        ffmpeg_resolver=lambda: Path("ffmpeg"),
        process_factory=process_factory,
        frame_source=lambda _: iter(frames),
        inpaint_operation=inpaint_operation,
    )


def test_export_streams_bgr_rawvideo_maps_optional_audio_and_renames_atomically(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "cleaned.mp4"
    frames = [np.full((2, 4, 3), value, dtype=np.uint8) for value in range(3)]
    factory = ProcessFactory()
    events = []
    exporter = make_exporter(factory, frames)

    result = exporter.export(
        export_request(source, output), CancellationToken(), on_progress=events.append
    )

    process = factory.processes[0]
    command = process.command
    assert result == output
    assert output.read_bytes() == b"partial"
    assert not (tmp_path / "cleaned.partial.mp4").exists()
    assert process.stdin.data == b"".join(frame.tobytes() for frame in frames)
    assert process.stdin.closed
    assert command[command.index("-f") + 1] == "rawvideo"
    assert command[command.index("-pixel_format") + 1] == "bgr24"
    assert command[command.index("-video_size") + 1] == "4x2"
    assert command[command.index("-framerate") + 1] == "2"
    assert [command[index + 1] for index, value in enumerate(command) if value == "-map"] == [
        "0:v:0",
        "1:a?",
    ]
    assert "-map_metadata" in command
    assert command[-1] == str(tmp_path / "cleaned.partial.mp4")
    assert [(event.state, event.frames_done) for event in events] == [
        (JobState.QUEUED, 0),
        (JobState.PROCESSING, 1),
        (JobState.PROCESSING, 2),
        (JobState.PROCESSING, 3),
        (JobState.COMPLETED, 3),
    ]
    assert all(event.frames_total == 3 for event in events)


def test_export_publishes_with_atomic_no_replace_without_os_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "cleaned.mp4"
    factory = ProcessFactory()
    real_rename = os.rename
    renames: list[tuple[Path, Path]] = []

    def disallow_replace(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("clobbering os.replace must not be used")

    def record_rename(source_path: str | Path, output_path: str | Path) -> None:
        renames.append((Path(source_path), Path(output_path)))
        real_rename(source_path, output_path)

    monkeypatch.setattr(export_module, "_platform_name", lambda: "windows", raising=False)
    monkeypatch.setattr(os, "replace", disallow_replace)
    monkeypatch.setattr(os, "rename", record_rename)

    result = make_exporter(
        factory, [np.zeros((2, 4, 3), dtype=np.uint8)]
    ).export(export_request(source, output), CancellationToken())

    partial = tmp_path / "cleaned.partial.mp4"
    assert result == output
    assert output.read_bytes() == b"partial"
    assert not partial.exists()
    assert renames == [(partial, output)]


def test_destination_appearing_during_publication_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "cleaned.mp4"
    partial = tmp_path / "cleaned.partial.mp4"
    factory = ProcessFactory()

    def disallow_replace(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("clobbering os.replace must not be used")

    def destination_wins(source_path: str | Path, output_path: str | Path) -> None:
        Path(output_path).write_bytes(b"someone-else")
        raise FileExistsError("destination appeared")

    monkeypatch.setattr(export_module, "_platform_name", lambda: "windows", raising=False)
    monkeypatch.setattr(os, "replace", disallow_replace)
    monkeypatch.setattr(os, "rename", destination_wins)

    with pytest.raises(ExportError, match="already exists"):
        make_exporter(
            factory, [np.zeros((2, 4, 3), dtype=np.uint8)]
        ).export(export_request(source, output), CancellationToken())

    assert output.read_bytes() == b"someone-else"
    assert not partial.exists()


def test_cancellation_after_publication_never_deletes_the_final_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "cleaned.mp4"
    token = CancellationToken()
    factory = ProcessFactory()
    publisher_name = (
        "_publish_no_replace"
        if hasattr(export_module, "_publish_no_replace")
        else "_publish_without_overwrite"
    )
    real_publish = getattr(export_module, publisher_name)

    def publish_then_compete(partial_path: Path, output_path: Path) -> None:
        real_publish(partial_path, output_path)
        output_path.write_bytes(b"someone-else")
        token.cancel()

    monkeypatch.setattr(export_module, publisher_name, publish_then_compete)

    result = make_exporter(
        factory, [np.zeros((2, 4, 3), dtype=np.uint8)]
    ).export(export_request(source, output), token)

    assert result == output
    assert output.read_bytes() == b"someone-else"
    assert not (tmp_path / "cleaned.partial.mp4").exists()


def test_preview_export_caps_video_and_audio_duration(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "preview.webm"
    frames = [np.zeros((2, 4, 3), dtype=np.uint8) for _ in range(8)]
    factory = ProcessFactory()
    exporter = make_exporter(factory, frames, reader=StaticMetadataReader(frames=8))

    exporter.export(export_request(source, output, 1.5), CancellationToken())

    process = factory.processes[0]
    assert len(process.stdin.data) == 3 * 2 * 4 * 3
    assert process.command[process.command.index("-t") + 1] == "1.5"
    assert ("-c:v", "libvpx-vp9") in tuple(
        zip(process.command, process.command[1:], strict=False)
    )


def test_export_refuses_existing_output_before_starting_ffmpeg(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"keep")
    factory = ProcessFactory()

    with pytest.raises(ExportError, match="already exists"):
        make_exporter(factory, []).export(
            export_request(source, output), CancellationToken()
        )

    assert output.read_bytes() == b"keep"
    assert factory.processes == []


def test_ffmpeg_failure_includes_safe_stderr_and_removes_partial(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "failed.mp4"
    factory = ProcessFactory(exit_code=1, stderr=b"encoder exploded\xff")

    with pytest.raises(ExportError, match="encoder exploded"):
        make_exporter(factory, [np.zeros((2, 4, 3), dtype=np.uint8)]).export(
            export_request(source, output), CancellationToken()
        )

    assert not output.exists()
    assert not (tmp_path / "failed.partial.mp4").exists()


def test_inpaint_exception_terminates_ffmpeg_and_removes_partial(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "failed.mp4"
    factory = ProcessFactory()

    def fail(*args: Any) -> np.ndarray:
        raise ValueError("bad region")

    with pytest.raises(ExportError, match="bad region"):
        make_exporter(
            factory,
            [np.zeros((2, 4, 3), dtype=np.uint8)],
            inpaint_operation=fail,
        ).export(export_request(source, output), CancellationToken())

    assert factory.processes[0].terminated
    assert not (tmp_path / "failed.partial.mp4").exists()


def test_cancellation_terminates_ffmpeg_and_removes_partial(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "cancelled.mp4"
    factory = ProcessFactory()
    token = CancellationToken()
    frames = [np.zeros((2, 4, 3), dtype=np.uint8) for _ in range(2)]

    def cancel_after_first(
        frame: np.ndarray, region: NormalizedRegion, options: ProcessingOptions
    ) -> np.ndarray:
        token.cancel()
        return frame

    with pytest.raises(CancelledError):
        make_exporter(factory, frames, inpaint_operation=cancel_after_first).export(
            export_request(source, output), token
        )

    assert factory.processes[0].terminated
    assert not output.exists()
    assert not (tmp_path / "cancelled.partial.mp4").exists()


def test_cancellation_while_ffmpeg_finalizes_terminates_process(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "cancelled.mp4"
    token = CancellationToken()

    class FinalizingProcess(FakeProcess):
        def wait(self, timeout: float | None = None) -> int:
            if not self.terminated:
                token.cancel()
                raise subprocess.TimeoutExpired(self.command, timeout or 0)
            return 0

    processes: list[FinalizingProcess] = []

    def create(command: list[str], **kwargs: Any) -> FinalizingProcess:
        process = FinalizingProcess(command)
        processes.append(process)
        return process

    exporter = VideoExporter(
        media_reader=StaticMetadataReader(frames=1),
        ffmpeg_resolver=lambda: Path("ffmpeg"),
        process_factory=create,
        frame_source=lambda _: iter([np.zeros((2, 4, 3), dtype=np.uint8)]),
        inpaint_operation=lambda frame, region, options: frame,
    )

    with pytest.raises(CancelledError):
        exporter.export(export_request(source, output), token)

    assert processes[0].terminated
    assert not output.exists()
    assert not (tmp_path / "cancelled.partial.mp4").exists()


def test_cancellation_after_successful_wait_prevents_publication(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "cancelled.mp4"
    token = CancellationToken()

    class CancelOnWaitProcess(FakeProcess):
        def wait(self, timeout: float | None = None) -> int:
            if not self.terminated:
                token.cancel()
            return 0

    processes: list[CancelOnWaitProcess] = []

    def create(command: list[str], **kwargs: Any) -> CancelOnWaitProcess:
        process = CancelOnWaitProcess(command)
        processes.append(process)
        return process

    exporter = VideoExporter(
        media_reader=StaticMetadataReader(frames=1),
        ffmpeg_resolver=lambda: Path("ffmpeg"),
        process_factory=create,
        frame_source=lambda _: iter([np.zeros((2, 4, 3), dtype=np.uint8)]),
        inpaint_operation=lambda frame, region, options: frame,
    )

    with pytest.raises(CancelledError):
        exporter.export(export_request(source, output), token)

    assert processes[0].terminated
    assert not output.exists()
    assert not (tmp_path / "cancelled.partial.mp4").exists()


def test_broken_pipe_failure_includes_sanitized_stderr_and_cleans_outputs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "failed.mp4"

    class BrokenStdin(RecordingStdin):
        def write(self, data: bytes) -> int:
            raise BrokenPipeError("pipe closed")

    class BrokenPipeProcess(FakeProcess):
        def __init__(self, command: list[str]) -> None:
            super().__init__(command)
            self.stdin = BrokenStdin()
            self.stderr = BytesIO(b"\x1b[31mencoder initialization failed")

    processes: list[BrokenPipeProcess] = []

    def create(command: list[str], **kwargs: Any) -> BrokenPipeProcess:
        process = BrokenPipeProcess(command)
        processes.append(process)
        return process

    exporter = VideoExporter(
        media_reader=StaticMetadataReader(frames=1),
        ffmpeg_resolver=lambda: Path("ffmpeg"),
        process_factory=create,
        frame_source=lambda _: iter([np.zeros((2, 4, 3), dtype=np.uint8)]),
        inpaint_operation=lambda frame, region, options: frame,
    )

    with pytest.raises(ExportError, match="encoder initialization failed") as captured:
        exporter.export(export_request(source, output), CancellationToken())

    assert "\x1b" not in str(captured.value)
    assert processes[0].terminated
    assert not output.exists()
    assert not (tmp_path / "failed.partial.mp4").exists()


def test_real_ffmpeg_tiny_video_round_trip_when_available(tmp_path: Path) -> None:
    try:
        ffmpeg = resolve_ffmpeg_executable()
    except FFmpegNotFoundError:
        pytest.skip("imageio-ffmpeg binary is not available")
    source = tmp_path / "tiny.mp4"
    generated = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=16x16:r=2:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    output = tmp_path / "tiny-cleaned.mp4"

    result = VideoExporter().export(
        export_request(source, output),
        CancellationToken(),
    )

    metadata = OpenCVMediaReader().probe(result)
    assert result.is_file()
    assert metadata.width == 16
    assert metadata.height == 16
    assert metadata.frame_count >= 1
