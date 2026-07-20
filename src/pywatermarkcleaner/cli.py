"""Command-line application boundary."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

from . import __version__
from .core.cancellation import CancelledError
from .core.exceptions import MediaError
from .core.export import VideoExporter, profile_for_suffix, resolve_export_path
from .core.geometry import NormalizedRegion
from .core.media import OpenCVMediaReader
from .core.models import (
    ExportRequest,
    InpaintMethod,
    JobState,
    ProcessingOptions,
    ProgressEvent,
    VideoMetadata,
)
from .core.scheduler import JobScheduler, ScheduledJob, max_allowed_workers


class MetadataReader(Protocol):
    def probe(self, path: Path) -> VideoMetadata: ...


class Scheduler(Protocol):
    def submit(
        self,
        request: ExportRequest,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> ScheduledJob: ...

    def cancel_all(self) -> None: ...

    def shutdown(self, *, wait: bool = True) -> None: ...


SchedulerFactory = Callable[[object, int], Scheduler]
OutputPathResolver = Callable[[Path, Path], Path]


def _windows_known_videos_directory() -> Path | None:
    """Return the Windows Known Folder Videos path when the shell provides it."""
    if os.name != "nt":
        return None

    class GUID(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        ]

    folder_id = GUID.from_buffer_copy(
        UUID("18989B1D-99B5-455B-841C-AB7C74E4DDFC").bytes_le
    )
    path_pointer = ctypes.c_wchar_p()
    try:
        windll = ctypes.LibraryLoader(ctypes.WinDLL)
        result = windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(path_pointer)
        )
        if result != 0 or not path_pointer.value:
            return None
        return Path(path_pointer.value)
    except (AttributeError, OSError):
        return None
    finally:
        if path_pointer.value:
            windll = ctypes.LibraryLoader(ctypes.WinDLL)
            windll.ole32.CoTaskMemFree(path_pointer)


def _expand_home(value: str, home: Path) -> Path:
    expanded = value.replace("${HOME}", str(home)).replace("$HOME", str(home))
    if expanded == "~":
        return home
    if expanded.startswith("~/") or expanded.startswith("~\\"):
        return home / expanded[2:]
    return Path(expanded)


def _linux_videos_directory(home: Path, environ: Mapping[str, str]) -> Path | None:
    configured_root = environ.get("XDG_CONFIG_HOME")
    config_root = (
        _expand_home(configured_root, home) if configured_root else home / ".config"
    )
    config_file = config_root / "user-dirs.dirs"
    try:
        lines = config_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        key, separator, raw_value = line.strip().partition("=")
        if separator and key == "XDG_VIDEOS_DIR":
            value = raw_value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            candidate = _expand_home(value, home)
            if candidate.is_absolute() and candidate != home:
                return candidate
            return None
    return None


def default_output_directory(
    platform_name: str | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    windows_videos_resolver: Callable[[], Path | None] | None = None,
) -> Path:
    """Resolve the platform-native, application-specific video output directory."""
    selected_platform = sys.platform if platform_name is None else platform_name
    selected_home = Path.home() if home is None else Path(home)
    selected_environment = os.environ if environ is None else environ

    if selected_platform == "win32":
        resolver = windows_videos_resolver or _windows_known_videos_directory
        videos_directory = resolver() or selected_home / "Videos"
    elif selected_platform == "darwin":
        videos_directory = selected_home / "Movies"
    else:
        videos_directory = (
            _linux_videos_directory(selected_home, selected_environment)
            or selected_home / "Videos"
        )
    return videos_directory / "PyWatermarkCleaner"


def _bounded_integer(label: str, minimum: int, maximum: int):  # type: ignore[no-untyped-def]
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"{label} must be from {minimum} to {maximum}"
            ) from error
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"{label} must be from {minimum} to {maximum}")
        return parsed

    return parse


def build_parser(maximum_workers: int | None = None) -> argparse.ArgumentParser:
    """Build the shared package and legacy command parser."""
    worker_limit = max_allowed_workers() if maximum_workers is None else maximum_workers
    parser = argparse.ArgumentParser(
        prog="pywatermarkcleaner",
        description="Remove a rectangular video watermark with inpainting.",
    )
    parser.add_argument(
        "-i",
        "--input",
        dest="input_paths",
        nargs="+",
        type=Path,
        required=True,
        help="input video path (one or more)",
    )
    parser.add_argument("--x", type=int, required=True, help="rectangle left edge in pixels")
    parser.add_argument("--y", type=int, required=True, help="rectangle top edge in pixels")
    parser.add_argument("--width", type=int, required=True, help="rectangle width in pixels")
    parser.add_argument("--height", type=int, required=True, help="rectangle height in pixels")
    parser.add_argument(
        "--thread",
        dest="workers",
        type=_bounded_integer("workers", 1, worker_limit),
        default=1,
        help="number of concurrent exports",
    )
    parser.add_argument(
        "--workers",
        dest="workers",
        type=_bounded_integer("workers", 1, worker_limit),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--preview", action="store_true", help="export only the first five seconds")
    parser.add_argument("--output-dir", type=Path, help="directory for cleaned videos")
    parser.add_argument(
        "--method",
        type=InpaintMethod,
        choices=tuple(InpaintMethod),
        default=InpaintMethod.TELEA,
        help="inpainting method",
    )
    parser.add_argument(
        "--radius",
        type=_bounded_integer("radius", 1, 10),
        default=3,
        help="inpainting radius from 1 to 10",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _reserve_output_path(
    input_path: Path,
    candidate: Path,
    reserved: set[Path],
) -> Path:
    if candidate not in reserved:
        reserved.add(candidate)
        return candidate

    base_name = f"{input_path.stem}_cleaned"
    index = 2
    while True:
        alternative = candidate.parent / f"{base_name}_{index}{candidate.suffix}"
        if not alternative.exists() and alternative not in reserved:
            reserved.add(alternative)
            return alternative
        index += 1


def _progress_reporter(path: Path) -> Callable[[ProgressEvent], None]:
    def report(event: ProgressEvent) -> None:
        if event.state != JobState.PROCESSING:
            return
        if event.frames_total:
            percent = min(100, int(event.frames_done * 100 / event.frames_total))
            print(f"{path}: Processing {percent}%")
        else:
            print(f"{path}: Processed {event.frames_done} frames")

    return report


def _run(
    arguments: argparse.Namespace,
    *,
    media_reader: MetadataReader,
    exporter: object,
    scheduler_factory: SchedulerFactory,
    output_directory_resolver: Callable[[], Path],
    output_path_resolver: OutputPathResolver,
) -> int:
    output_directory = (
        Path(arguments.output_dir)
        if arguments.output_dir is not None
        else Path(output_directory_resolver())
    )
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"Could not create output directory '{output_directory}': {error}", file=sys.stderr)
        return 1

    requests: list[ExportRequest] = []
    reserved_outputs: set[Path] = set()
    had_validation_error = False
    had_processing_failure = False
    options = ProcessingOptions(arguments.method, arguments.radius)

    for input_path in arguments.input_paths:
        input_path = Path(input_path)
        try:
            source_metadata = media_reader.probe(input_path)
        except (MediaError, OSError, ValueError) as error:
            print(f"Failed {input_path}: {error}", file=sys.stderr)
            had_processing_failure = True
            continue
        except Exception as error:
            print(f"Failed {input_path}: {error}", file=sys.stderr)
            had_processing_failure = True
            continue

        try:
            region = NormalizedRegion.from_cli_pixels(
                arguments.x,
                arguments.y,
                arguments.width,
                arguments.height,
                source_metadata.width,
                source_metadata.height,
            )
        except ValueError as error:
            print(f"Invalid rectangle for {input_path}: {error}", file=sys.stderr)
            had_validation_error = True
            continue

        profile = profile_for_suffix(input_path.suffix)
        if profile.notice is not None:
            print(f"{input_path}: {profile.notice}", file=sys.stderr)
        try:
            candidate = Path(output_path_resolver(input_path, output_directory))
            output_path = _reserve_output_path(input_path, candidate, reserved_outputs)
            if output_path.resolve() == input_path.resolve():
                raise ValueError("the output path would overwrite the input")
        except (OSError, ValueError) as error:
            print(f"Failed {input_path}: Could not select a safe output: {error}", file=sys.stderr)
            had_processing_failure = True
            continue

        requests.append(
            ExportRequest(
                input_path=input_path,
                output_path=output_path,
                region=region,
                options=options,
                preview_seconds=5.0 if arguments.preview else None,
            )
        )

    scheduler: Scheduler | None = None
    interrupted = False
    if requests:
        try:
            scheduler = scheduler_factory(exporter, arguments.workers)
            scheduled: list[tuple[ExportRequest, ScheduledJob]] = []
            for request in requests:
                try:
                    job = scheduler.submit(
                        request, on_progress=_progress_reporter(request.input_path)
                    )
                    scheduled.append((request, job))
                except Exception as error:
                    print(f"Failed {request.input_path}: {error}", file=sys.stderr)
                    had_processing_failure = True

            for request, job in scheduled:
                try:
                    completed_path = job.future.result()
                    print(f"Completed {request.input_path} -> {completed_path}")
                except CancelledError:
                    print(f"Canceled {request.input_path}.", file=sys.stderr)
                    had_processing_failure = True
                except Exception as error:
                    print(f"Failed {request.input_path}: {error}", file=sys.stderr)
                    had_processing_failure = True
        except KeyboardInterrupt:
            interrupted = True
            if scheduler is not None:
                scheduler.cancel_all()
            print("Canceled all jobs.", file=sys.stderr)
        except Exception as error:
            print(f"Could not start processing: {error}", file=sys.stderr)
            had_processing_failure = True
        finally:
            if scheduler is not None:
                try:
                    scheduler.shutdown(wait=True)
                except Exception as error:
                    print(f"Could not finish scheduler shutdown: {error}", file=sys.stderr)
                    had_processing_failure = True

    if interrupted:
        return 130
    if had_validation_error:
        return 2
    if had_processing_failure:
        return 1
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    maximum_workers: int | None = None,
    media_reader: MetadataReader | None = None,
    exporter: object | None = None,
    scheduler_factory: SchedulerFactory = JobScheduler,  # type: ignore[assignment]
    output_directory_resolver: Callable[[], Path] = default_output_directory,
    output_path_resolver: OutputPathResolver = resolve_export_path,
) -> int:
    """Parse command-line arguments and return a documented process status."""
    try:
        arguments = build_parser(maximum_workers).parse_args(argv)
    except SystemExit as exit_request:
        return exit_request.code if isinstance(exit_request.code, int) else 1
    return _run(
        arguments,
        media_reader=media_reader or OpenCVMediaReader(),
        exporter=exporter or VideoExporter(),
        scheduler_factory=scheduler_factory,
        output_directory_resolver=output_directory_resolver,
        output_path_resolver=output_path_resolver,
    )
