"""Frame-free, path-sanitized diagnostic text for support."""

from __future__ import annotations

import platform
from pathlib import Path

import cv2
import imageio_ffmpeg  # type: ignore[import-untyped]
from PySide6.QtCore import qVersion

from pywatermarkcleaner import __version__
from pywatermarkcleaner.core.models import VideoMetadata


def _ffmpeg_version() -> str:
    try:
        return str(imageio_ffmpeg.get_ffmpeg_version())
    except Exception:
        return "unavailable"


def _sanitize_error(text: str) -> str:
    home = str(Path.home())
    return text.replace(home, "<home>")


def build_diagnostics(metadata: list[VideoMetadata], errors: list[str]) -> str:
    lines = [
        f"App: PyWatermarkCleaner {__version__}",
        f"Python: {platform.python_version()}",
        f"Qt: {qVersion()}",
        f"OpenCV: {cv2.__version__}",
        f"FFmpeg: {_ffmpeg_version()}",
        f"Platform: {platform.platform()}",
        "Media:",
    ]
    if metadata:
        for item in metadata:
            lines.append(
                f"- {item.path.name}: {item.width}x{item.height}, "
                f"{item.duration_seconds:.2f}s, {item.container}"
            )
    else:
        lines.append("- none")
    lines.append("Job errors:")
    lines.extend(f"- {_sanitize_error(error)}" for error in errors if error)
    if not errors:
        lines.append("- none")
    return "\n".join(lines)
