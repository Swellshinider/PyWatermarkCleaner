"""Safe FFmpeg export profiles and full-resolution raw-frame streaming."""

from __future__ import annotations

import ctypes
import errno
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Protocol, cast

import cv2
import imageio_ffmpeg  # type: ignore[import-untyped]
import numpy as np
from numpy.typing import NDArray

from .cancellation import CancellationToken, CancelledError
from .exceptions import ExportError, FFmpegNotFoundError, MediaError
from .geometry import NormalizedRegion
from .inpainting import inpaint_frame
from .media import OpenCVMediaReader
from .models import ExportRequest, JobState, ProcessingOptions, ProgressEvent, VideoMetadata
from .output_paths import allocate_output_path


@dataclass(frozen=True, slots=True)
class ExportProfile:
    """FFmpeg video settings selected from an output container suffix."""

    extension: str
    video_args: tuple[str, ...]
    notice: str | None = None

    def pixel_format(self, width: int, height: int) -> str:
        """Choose a codec-compatible format without resizing odd H.264 frames."""
        is_h264 = "libx264" in self.video_args
        if is_h264 and (width % 2 or height % 2):
            return "yuv444p"
        return "yuv420p"


_PROFILES = {
    extension: ExportProfile(
        extension,
        ("-c:v", "libx264", "-crf", "18", "-preset", "medium"),
    )
    for extension in ("mp4", "mov", "m4v", "mkv")
}
_PROFILES.update(
    {
        "avi": ExportProfile("avi", ("-c:v", "mpeg4", "-q:v", "3")),
        "webm": ExportProfile(
            "webm", ("-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0")
        ),
    }
)


def profile_for_suffix(suffix: str) -> ExportProfile:
    """Return settings for ``suffix``, visibly falling back to MP4 when unsupported."""
    normalized = suffix.lower().lstrip(".")
    if normalized in _PROFILES:
        return _PROFILES[normalized]
    shown = normalized or "(none)"
    notice = f"Unsupported output container '{shown}'; exporting as MP4 instead."
    return replace(_PROFILES["mp4"], notice=notice)


def resolve_export_path(input_path: Path, output_dir: Path) -> Path:
    """Allocate a collision-safe output using the selected or fallback container."""
    profile = profile_for_suffix(Path(input_path).suffix)
    return allocate_output_path(input_path, output_dir, profile.extension)


def resolve_ffmpeg_executable() -> Path:
    """Resolve a usable configured or imageio-ffmpeg-managed executable."""
    configured = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate
    try:
        candidate = Path(imageio_ffmpeg.get_ffmpeg_exe()).expanduser()
    except Exception as error:
        raise FFmpegNotFoundError(
            "FFmpeg was not found. Install imageio-ffmpeg or set IMAGEIO_FFMPEG_EXE "
            "to a valid executable."
        ) from error
    if not candidate.is_file():
        raise FFmpegNotFoundError(
            "FFmpeg was not found. Install imageio-ffmpeg or set IMAGEIO_FFMPEG_EXE "
            "to a valid executable."
        )
    return candidate


class MetadataReader(Protocol):
    def probe(self, path: Path) -> VideoMetadata: ...


class WritableProcess(Protocol):
    stdin: BinaryIO | None
    stderr: BinaryIO | None

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[..., WritableProcess]
FrameSource = Callable[[Path], Iterable[NDArray[np.uint8]]]
InpaintOperation = Callable[
    [NDArray[np.uint8], NormalizedRegion, ProcessingOptions], NDArray[np.uint8]
]


def _opencv_frames(path: Path) -> Iterable[NDArray[np.uint8]]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise MediaError(f"Could not open media '{path}' for full-resolution export.")
    try:
        while True:
            succeeded, frame = capture.read()
            if not succeeded:
                break
            yield cast(NDArray[np.uint8], frame)
    finally:
        capture.release()


def _safe_stderr(process: WritableProcess) -> str:
    if process.stderr is None:
        return ""
    raw = process.stderr.read()
    if isinstance(raw, str):
        text = raw
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.replace("\x1b", "?").strip()[-2000:]


def _terminate(process: WritableProcess) -> None:
    try:
        process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _wait_for_ffmpeg(process: WritableProcess, token: CancellationToken) -> int:
    while True:
        token.raise_if_cancelled()
        try:
            return process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            continue


def _platform_name() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "posix"


def _linux_rename_no_replace(partial_path: Path, output_path: Path) -> bool:
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError):
        return False
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(partial_path),
        -100,
        os.fsencode(output_path),
        1,
    )
    if result == 0:
        return True
    error_number = ctypes.get_errno()
    unsupported = {
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    if error_number in unsupported:
        return False
    raise OSError(error_number, os.strerror(error_number), output_path)


def _macos_rename_no_replace(partial_path: Path, output_path: Path) -> bool:
    try:
        renamex_np = ctypes.CDLL(None, use_errno=True).renamex_np
    except (AttributeError, OSError):
        return False
    renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex_np.restype = ctypes.c_int
    result = renamex_np(os.fsencode(partial_path), os.fsencode(output_path), 0x00000004)
    if result == 0:
        return True
    error_number = ctypes.get_errno()
    unsupported = {
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    if error_number in unsupported:
        return False
    raise OSError(error_number, os.strerror(error_number), output_path)


def _hardlink_no_replace(partial_path: Path, output_path: Path) -> None:
    os.link(partial_path, output_path)
    try:
        partial_path.unlink()
    except OSError as error:
        raise error


def _publish_no_replace(partial_path: Path, output_path: Path) -> None:
    """Atomically publish ``partial_path`` without replacing an existing destination."""
    try:
        platform = _platform_name()
        if platform == "windows":
            os.rename(partial_path, output_path)
            return
        if platform == "linux" and _linux_rename_no_replace(partial_path, output_path):
            return
        if platform == "macos" and _macos_rename_no_replace(partial_path, output_path):
            return
        _hardlink_no_replace(partial_path, output_path)
    except OSError as error:
        if isinstance(error, FileExistsError) or error.errno == errno.EEXIST or getattr(
            error, "winerror", None
        ) in {80, 183}:
            raise ExportError(
                f"Output already exists and will not be overwritten: {output_path}"
            ) from error
        raise ExportError(f"Could not finalize output '{output_path}': {error}") from error


class VideoExporter:
    """Inpaint full-resolution frames and stream them to a managed FFmpeg process."""

    def __init__(
        self,
        *,
        media_reader: MetadataReader | None = None,
        ffmpeg_resolver: Callable[[], Path] = resolve_ffmpeg_executable,
        process_factory: ProcessFactory | None = None,
        frame_source: FrameSource = _opencv_frames,
        inpaint_operation: InpaintOperation = inpaint_frame,
    ) -> None:
        self._media_reader = media_reader or OpenCVMediaReader()
        self._ffmpeg_resolver = ffmpeg_resolver
        self._process_factory = process_factory or cast(ProcessFactory, subprocess.Popen)
        self._frame_source = frame_source
        self._inpaint = inpaint_operation

    def export(
        self,
        request: ExportRequest,
        token: CancellationToken,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> Path:
        output_path = Path(request.output_path)
        if output_path.exists():
            raise ExportError(f"Output already exists and will not be overwritten: {output_path}")
        if output_path.resolve() == Path(request.input_path).resolve():
            raise ExportError(
                "Input media will never be overwritten; choose a different output path."
            )

        metadata = self._media_reader.probe(request.input_path)
        profile = profile_for_suffix(output_path.suffix)
        partial_path = output_path.with_name(f"{output_path.stem}.partial{output_path.suffix}")
        if partial_path.exists():
            partial_path.unlink()

        frames_total: int | None = metadata.frame_count or None
        if request.preview_seconds is not None:
            preview_frames = max(1, int(request.preview_seconds * metadata.fps))
            frames_total = (
                min(metadata.frame_count, preview_frames)
                if metadata.frame_count
                else preview_frames
            )
        job_id = output_path.stem

        def progress(state: JobState, frames_done: int, message: str = "") -> None:
            if on_progress is not None:
                on_progress(ProgressEvent(job_id, state, frames_done, frames_total, message))

        progress(JobState.QUEUED, 0, "Queued for export")
        token.raise_if_cancelled()
        command = self._command(request, metadata, profile, partial_path)
        process: WritableProcess | None = None
        frames_done = 0
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            process = self._process_factory(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            if process.stdin is None:
                raise ExportError("FFmpeg did not provide a writable input pipe.")
            for frame in self._frame_source(request.input_path):
                if frames_total is not None and frames_done >= frames_total:
                    break
                token.raise_if_cancelled()
                self._validate_frame(frame, metadata)
                cleaned = self._inpaint(frame, request.region, request.options)
                token.raise_if_cancelled()
                process.stdin.write(cleaned.tobytes())
                frames_done += 1
                progress(JobState.PROCESSING, frames_done, "Processing frames")
            process.stdin.close()
            token.raise_if_cancelled()
            exit_code = _wait_for_ffmpeg(process, token)
            token.raise_if_cancelled()
            if exit_code != 0:
                detail = _safe_stderr(process)
                context = f": {detail}" if detail else ""
                raise ExportError(f"FFmpeg export failed with exit code {exit_code}{context}")
            if not partial_path.is_file():
                raise ExportError("FFmpeg completed without creating the partial output file.")
            token.raise_if_cancelled()
            _publish_no_replace(partial_path, output_path)
            progress(JobState.COMPLETED, frames_done, "Export completed")
            return output_path
        except CancelledError:
            if process is not None:
                _terminate(process)
            partial_path.unlink(missing_ok=True)
            raise
        except Exception as error:
            detail = ""
            if process is not None:
                _terminate(process)
                try:
                    detail = _safe_stderr(process)
                except (OSError, ValueError):
                    pass
            partial_path.unlink(missing_ok=True)
            if isinstance(error, ExportError):
                raise
            context = f"; FFmpeg: {detail}" if detail else ""
            raise ExportError(
                f"Could not export '{request.input_path}': {error}{context}"
            ) from error

    @staticmethod
    def _validate_frame(frame: NDArray[np.uint8], metadata: VideoMetadata) -> None:
        expected = (metadata.height, metadata.width, 3)
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.shape != expected:
            raise ExportError(
                f"Decoded frame has shape/dtype {getattr(frame, 'shape', None)}/"
                f"{getattr(frame, 'dtype', None)}; expected uint8 BGR {expected}."
            )

    def _command(
        self,
        request: ExportRequest,
        metadata: VideoMetadata,
        profile: ExportProfile,
        partial_path: Path,
    ) -> list[str]:
        command = [
            str(self._ffmpeg_resolver()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pixel_format",
            "bgr24",
            "-video_size",
            f"{metadata.width}x{metadata.height}",
            "-framerate",
            f"{metadata.fps:g}",
            "-i",
            "pipe:0",
            "-i",
            str(request.input_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-map_metadata",
            "1",
            *profile.video_args,
            "-pix_fmt",
            profile.pixel_format(metadata.width, metadata.height),
            "-c:a",
            "copy",
        ]
        if request.preview_seconds is not None:
            command.extend(("-t", f"{request.preview_seconds:g}"))
        command.append(str(partial_path))
        return command
