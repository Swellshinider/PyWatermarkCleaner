from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from pywatermarkcleaner.core.exceptions import FFmpegNotFoundError
from pywatermarkcleaner.core.export import resolve_ffmpeg_executable


@pytest.fixture(scope="session")
def ffmpeg_executable() -> Path:
    try:
        return resolve_ffmpeg_executable()
    except FFmpegNotFoundError:
        pytest.skip("imageio-ffmpeg binary is not available")


@pytest.fixture
def synthetic_video_factory(tmp_path: Path, ffmpeg_executable: Path) -> Callable[..., Path]:
    def create(*, name: str, with_audio: bool, duration: float = 1.0) -> Path:
        output = tmp_path / name
        command = [
            str(ffmpeg_executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x176B87:s=64x48:r=10:d={duration:g}",
        ]
        if with_audio:
            command.extend(
                (
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency=440:sample_rate=44100:duration={duration:g}",
                )
            )
        command.extend(("-c:v", "libx264", "-pix_fmt", "yuv420p"))
        if with_audio:
            command.extend(("-c:a", "aac", "-shortest"))
        command.extend(
            (
                "-metadata",
                "title=PyWatermarkCleaner synthetic fixture",
                str(output),
            )
        )
        completed = subprocess.run(command, capture_output=True, check=False)
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        return output

    return create
