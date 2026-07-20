from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path

import pytest

from pywatermarkcleaner import cli
from pywatermarkcleaner.core.exceptions import ExportError, MediaError
from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.models import (
    ExportRequest,
    InpaintMethod,
    JobState,
    ProgressEvent,
    VideoMetadata,
)
from pywatermarkcleaner.core.scheduler import ScheduledJob


def required_arguments(input_flag: str = "-i") -> list[str]:
    return [
        input_flag,
        "clip.mp4",
        "--x",
        "10",
        "--y",
        "20",
        "--width",
        "30",
        "--height",
        "40",
    ]


@pytest.mark.parametrize("input_flag", ["-i", "--input"])
@pytest.mark.parametrize("worker_flag", ["--thread", "--workers"])
def test_parser_accepts_legacy_and_new_aliases(
    input_flag: str, worker_flag: str
) -> None:
    parser = cli.build_parser(maximum_workers=4)

    arguments = parser.parse_args(
        [
            input_flag,
            "clip.mp4",
            "second.mov",
            "--x",
            "10",
            "--y",
            "20",
            "--width",
            "30",
            "--height",
            "40",
            worker_flag,
            "2",
            "--preview",
            "--output-dir",
            "exports",
            "--method",
            "navier-stokes",
            "--radius",
            "7",
        ]
    )

    assert arguments.input_paths == [Path("clip.mp4"), Path("second.mov")]
    assert (arguments.x, arguments.y, arguments.width, arguments.height) == (10, 20, 30, 40)
    assert arguments.workers == 2
    assert arguments.preview is True
    assert arguments.output_dir == Path("exports")
    assert arguments.method == InpaintMethod.NAVIER_STOKES
    assert arguments.radius == 7


def test_parser_defaults_match_cli_contract() -> None:
    arguments = cli.build_parser(maximum_workers=4).parse_args(required_arguments())

    assert arguments.workers == 1
    assert arguments.preview is False
    assert arguments.output_dir is None
    assert arguments.method == InpaintMethod.TELEA
    assert arguments.radius == 3


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--thread", "0"], "workers must be from 1 to 3"),
        (["--workers", "4"], "workers must be from 1 to 3"),
        (["--radius", "0"], "radius must be from 1 to 10"),
        (["--radius", "11"], "radius must be from 1 to 10"),
    ],
)
def test_parser_validation_returns_two_with_exact_error(
    extra: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    result = cli.main([*required_arguments(), *extra], maximum_workers=3)

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.endswith(f"pywatermarkcleaner: error: argument {extra[0]}: {message}\n")
    assert "Traceback" not in captured.err


def test_version_output_and_code_are_exact(capsys: pytest.CaptureFixture[str]) -> None:
    result = cli.main(["--version"])

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == "pywatermarkcleaner 1.0.0\n"
    assert captured.err == ""


def test_windows_output_directory_uses_known_folder_without_creating_it(
    tmp_path: Path,
) -> None:
    videos = tmp_path / "Native Videos"

    result = cli.default_output_directory(
        platform_name="win32",
        home=tmp_path / "home",
        windows_videos_resolver=lambda: videos,
    )

    assert result == videos / "PyWatermarkCleaner"
    assert not result.exists()


def test_windows_output_directory_falls_back_to_home_videos(tmp_path: Path) -> None:
    home = tmp_path / "home"

    result = cli.default_output_directory(
        platform_name="win32",
        home=home,
        windows_videos_resolver=lambda: None,
    )

    assert result == home / "Videos" / "PyWatermarkCleaner"


def test_macos_output_directory_uses_home_movies(tmp_path: Path) -> None:
    home = tmp_path / "home"

    result = cli.default_output_directory(platform_name="darwin", home=home)

    assert result == home / "Movies" / "PyWatermarkCleaner"


@pytest.mark.parametrize(
    ("configured", "relative"),
    [
        ('$HOME/Creator Videos', Path("Creator Videos")),
        ('${HOME}/Videos', Path("Videos")),
    ],
)
def test_linux_output_directory_expands_xdg_home(
    configured: str, relative: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    config = home / ".config"
    config.mkdir(parents=True)
    (config / "user-dirs.dirs").write_text(
        f'XDG_DOWNLOAD_DIR="$HOME/Downloads"\nXDG_VIDEOS_DIR="{configured}"\n',
        encoding="utf-8",
    )

    result = cli.default_output_directory(
        platform_name="linux", home=home, environ={}
    )

    assert result == home / relative / "PyWatermarkCleaner"


def test_linux_output_directory_honors_xdg_config_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = tmp_path / "xdg-config"
    config.mkdir()
    videos = tmp_path / "shared-videos"
    (config / "user-dirs.dirs").write_text(
        f'XDG_VIDEOS_DIR="{videos}"\n', encoding="utf-8"
    )

    result = cli.default_output_directory(
        platform_name="linux",
        home=home,
        environ={"XDG_CONFIG_HOME": str(config)},
    )

    assert result == videos / "PyWatermarkCleaner"


@pytest.mark.parametrize("contents", [None, "", 'XDG_VIDEOS_DIR="$HOME"\n'])
def test_linux_output_directory_falls_back_to_home_videos(
    contents: str | None, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    if contents is not None:
        config = home / ".config"
        config.mkdir(parents=True)
        (config / "user-dirs.dirs").write_text(contents, encoding="utf-8")

    result = cli.default_output_directory(
        platform_name="linux", home=home, environ={}
    )

    assert result == home / "Videos" / "PyWatermarkCleaner"


class StubMediaReader:
    def __init__(
        self,
        metadata: dict[Path, VideoMetadata],
        failures: dict[Path, Exception] | None = None,
    ) -> None:
        self.metadata = metadata
        self.failures = failures or {}
        self.probed: list[Path] = []

    def probe(self, path: Path) -> VideoMetadata:
        self.probed.append(path)
        if path in self.failures:
            raise self.failures[path]
        return self.metadata[path]


class StubScheduler:
    def __init__(
        self,
        exporter: object,
        max_workers: int,
        failures: dict[Path, BaseException] | None = None,
    ) -> None:
        self.exporter = exporter
        self.max_workers = max_workers
        self.failures = failures or {}
        self.requests: list[ExportRequest] = []
        self.cancel_all_called = False
        self.shutdown_wait: bool | None = None

    def submit(
        self,
        request: ExportRequest,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> ScheduledJob:
        self.requests.append(request)
        if on_progress is not None:
            on_progress(ProgressEvent("ignored", JobState.PROCESSING, 5, 10))
        future: Future[Path] = Future()
        failure = self.failures.get(request.input_path)
        if failure is None:
            future.set_result(request.output_path)
        else:
            future.set_exception(failure)
        return ScheduledJob(f"fake-{len(self.requests)}", future)

    def cancel_all(self) -> None:
        self.cancel_all_called = True

    def shutdown(self, *, wait: bool = True) -> None:
        self.shutdown_wait = wait


def metadata(path: Path, width: int, height: int) -> VideoMetadata:
    return VideoMetadata(path, width, height, 25.0, 250, 10.0, path.suffix[1:])


def scheduler_factory(
    observed: list[StubScheduler],
    failures: dict[Path, BaseException] | None = None,
) -> Callable[[object, int], StubScheduler]:
    def create(exporter: object, max_workers: int) -> StubScheduler:
        scheduler = StubScheduler(exporter, max_workers, failures)
        observed.append(scheduler)
        return scheduler

    return create


def test_run_submits_mixed_dimensions_with_options_preview_and_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    landscape = Path("landscape.mov")
    portrait = Path("portrait.mov")
    reader = StubMediaReader(
        {
            landscape: metadata(landscape, 100, 50),
            portrait: metadata(portrait, 50, 100),
        }
    )
    schedulers: list[StubScheduler] = []
    output_dir = tmp_path / "exports"

    result = cli.main(
        [
            "-i",
            str(landscape),
            str(portrait),
            "--x",
            "-20",
            "--y",
            "-10",
            "--width",
            "30",
            "--height",
            "20",
            "--thread",
            "2",
            "--preview",
            "--method",
            "navier-stokes",
            "--radius",
            "8",
            "--output-dir",
            str(output_dir),
        ],
        maximum_workers=4,
        media_reader=reader,
        exporter=object(),
        scheduler_factory=scheduler_factory(schedulers),
    )

    assert result == 0
    assert output_dir.is_dir()
    assert reader.probed == [landscape, portrait]
    assert len(schedulers) == 1
    scheduler = schedulers[0]
    assert scheduler.max_workers == 2
    assert [request.region for request in scheduler.requests] == [
        NormalizedRegion.from_cli_pixels(-20, -10, 30, 20, 100, 50),
        NormalizedRegion.from_cli_pixels(-20, -10, 30, 20, 50, 100),
    ]
    assert [request.options.method for request in scheduler.requests] == [
        InpaintMethod.NAVIER_STOKES,
        InpaintMethod.NAVIER_STOKES,
    ]
    assert [request.options.radius for request in scheduler.requests] == [8, 8]
    assert [request.preview_seconds for request in scheduler.requests] == [5.0, 5.0]
    assert [request.output_path for request in scheduler.requests] == [
        output_dir / "landscape_cleaned.mov",
        output_dir / "portrait_cleaned.mov",
    ]
    assert scheduler.shutdown_wait is True
    captured = capsys.readouterr()
    assert "landscape.mov: Processing 50%" in captured.out
    assert "portrait.mov: Processing 50%" in captured.out
    assert "Completed landscape.mov ->" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_run_uses_default_output_only_when_execution_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = Path("clip.mp4")
    output_dir = tmp_path / "default"
    reader = StubMediaReader({source: metadata(source, 100, 100)})
    schedulers: list[StubScheduler] = []
    resolver_calls = 0

    def resolve_default() -> Path:
        nonlocal resolver_calls
        resolver_calls += 1
        return output_dir

    assert cli.main(["--version"], output_directory_resolver=resolve_default) == 0
    assert resolver_calls == 0
    assert not output_dir.exists()
    capsys.readouterr()

    result = cli.main(
        required_arguments(),
        media_reader=reader,
        exporter=object(),
        scheduler_factory=scheduler_factory(schedulers),
        output_directory_resolver=resolve_default,
    )

    assert result == 0
    assert resolver_calls == 1
    assert output_dir.is_dir()


def test_run_allocates_batch_collisions_and_reports_container_fallback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = Path("one") / "clip.wmv"
    second = Path("two") / "clip.wmv"
    reader = StubMediaReader(
        {first: metadata(first, 100, 100), second: metadata(second, 100, 100)}
    )
    output_dir = tmp_path / "exports"
    output_dir.mkdir()
    (output_dir / "clip_cleaned.mp4").touch()
    schedulers: list[StubScheduler] = []

    result = cli.main(
        [
            "-i",
            str(first),
            str(second),
            "--x",
            "0",
            "--y",
            "0",
            "--width",
            "10",
            "--height",
            "10",
            "--output-dir",
            str(output_dir),
        ],
        media_reader=reader,
        exporter=object(),
        scheduler_factory=scheduler_factory(schedulers),
    )

    assert result == 0
    assert [request.output_path for request in schedulers[0].requests] == [
        output_dir / "clip_cleaned_2.mp4",
        output_dir / "clip_cleaned_3.mp4",
    ]
    captured = capsys.readouterr()
    assert "Unsupported output container 'wmv'; exporting as MP4 instead." in captured.err


def test_run_continues_after_processing_failure_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sources = [Path("good.mp4"), Path("failed.mp4"), Path("also-good.mp4")]
    reader = StubMediaReader({path: metadata(path, 100, 100) for path in sources})
    schedulers: list[StubScheduler] = []

    result = cli.main(
        [
            "-i",
            *(str(path) for path in sources),
            "--x",
            "0",
            "--y",
            "0",
            "--width",
            "10",
            "--height",
            "10",
            "--output-dir",
            str(tmp_path),
        ],
        media_reader=reader,
        exporter=object(),
        scheduler_factory=scheduler_factory(
            schedulers, {sources[1]: ExportError("encoder stopped")}
        ),
    )

    assert result == 1
    assert [request.input_path for request in schedulers[0].requests] == sources
    captured = capsys.readouterr()
    assert "Completed good.mp4 ->" in captured.out
    assert "Completed also-good.mp4 ->" in captured.out
    assert "Failed failed.mp4: encoder stopped" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_run_reports_invalid_media_and_processes_other_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid = Path("invalid.mp4")
    valid = Path("valid.mp4")
    reader = StubMediaReader(
        {valid: metadata(valid, 100, 100)},
        {invalid: MediaError("codec is unreadable")},
    )
    schedulers: list[StubScheduler] = []

    result = cli.main(
        [
            "-i",
            str(invalid),
            str(valid),
            "--x",
            "0",
            "--y",
            "0",
            "--width",
            "10",
            "--height",
            "10",
            "--output-dir",
            str(tmp_path),
        ],
        media_reader=reader,
        exporter=object(),
        scheduler_factory=scheduler_factory(schedulers),
    )

    assert result == 1
    assert [request.input_path for request in schedulers[0].requests] == [valid]
    captured = capsys.readouterr()
    assert "Failed invalid.mp4: codec is unreadable" in captured.err
    assert "Completed valid.mp4 ->" in captured.out


@pytest.mark.parametrize(
    ("rectangle", "reason"),
    [
        ((0, 0, 0, 10), "region width and height must be positive"),
        ((100, 100, 10, 10), "region does not overlap the frame"),
    ],
)
def test_run_reports_invalid_rectangle_with_path_and_returns_two(
    rectangle: tuple[int, int, int, int],
    reason: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = Path("affected.mp4")
    reader = StubMediaReader({source: metadata(source, 100, 100)})
    schedulers: list[StubScheduler] = []
    x, y, width, height = rectangle

    result = cli.main(
        [
            "-i",
            str(source),
            "--x",
            str(x),
            "--y",
            str(y),
            "--width",
            str(width),
            "--height",
            str(height),
            "--output-dir",
            str(tmp_path),
        ],
        media_reader=reader,
        exporter=object(),
        scheduler_factory=scheduler_factory(schedulers),
    )

    assert result == 2
    assert schedulers == []
    captured = capsys.readouterr()
    assert f"Invalid rectangle for affected.mp4: {reason}" in captured.err
    assert "Traceback" not in captured.err


def test_keyboard_interrupt_cancels_all_jobs_and_returns_130(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    interrupted = Path("interrupt.mp4")
    queued = Path("queued.mp4")
    reader = StubMediaReader(
        {
            interrupted: metadata(interrupted, 100, 100),
            queued: metadata(queued, 100, 100),
        }
    )
    schedulers: list[StubScheduler] = []

    result = cli.main(
        [
            "-i",
            str(interrupted),
            str(queued),
            "--x",
            "0",
            "--y",
            "0",
            "--width",
            "10",
            "--height",
            "10",
            "--output-dir",
            str(tmp_path),
        ],
        media_reader=reader,
        exporter=object(),
        scheduler_factory=scheduler_factory(
            schedulers, {interrupted: KeyboardInterrupt()}
        ),
    )

    assert result == 130
    assert len(schedulers[0].requests) == 2
    assert schedulers[0].cancel_all_called is True
    assert schedulers[0].shutdown_wait is True
    captured = capsys.readouterr()
    assert "Canceled all jobs." in captured.err
    assert "Traceback" not in captured.err
