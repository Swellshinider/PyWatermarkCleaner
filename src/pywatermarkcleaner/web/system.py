"""Persisted settings, native pickers, reveal-in-folder and ffmpeg helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pywatermarkcleaner.cli import default_output_directory
from pywatermarkcleaner.core.exceptions import MediaError
from pywatermarkcleaner.core.export import resolve_ffmpeg_executable
from pywatermarkcleaner.core.models import FormatPolicy, InpaintMethod, PerformanceMode
from pywatermarkcleaner.core.scheduler import max_allowed_workers

VIDEO_PATTERNS = "*.mp4 *.mov *.m4v *.mkv *.avi *.webm"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def config_directory() -> Path:
    """Platform-native per-user configuration directory for the application."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "PyWatermarkCleaner"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass
class Settings:
    """Validated preferences persisted as JSON."""

    method: str = InpaintMethod.TELEA.value
    radius: int = 3
    performance: str = PerformanceMode.BALANCED.value
    workers: int = 1
    output_folder: str = field(default_factory=lambda: str(default_output_directory()))
    format_policy: str = FormatPolicy.ORIGINAL.value

    def apply(self, values: dict[str, Any]) -> None:
        """Validate every field of ``values`` first, then assign; raises ``ValueError``."""
        clean: dict[str, Any] = {}
        for key, value in values.items():
            if key == "method" and value in {m.value for m in InpaintMethod}:
                clean[key] = value
            elif key == "performance" and value in {m.value for m in PerformanceMode}:
                clean[key] = value
            elif key == "format_policy" and value in {m.value for m in FormatPolicy}:
                clean[key] = value
            elif key == "radius" and _is_int(value) and 1 <= value <= 10:
                clean[key] = value
            elif key == "workers" and _is_int(value) and 1 <= value <= max_allowed_workers():
                clean[key] = value
            elif key == "output_folder" and isinstance(value, str) and value.strip():
                clean[key] = str(Path(value.strip()).expanduser().resolve())
            else:
                raise ValueError(f"Invalid value for setting '{key}'.")
        for key, value in clean.items():
            setattr(self, key, value)


def load_settings(path: Path) -> Settings:
    """Read settings, falling back to the default for any missing or invalid field."""
    settings = Settings()
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if isinstance(stored, dict):
        for key, value in stored.items():
            try:
                settings.apply({key: value})
            except ValueError:
                continue
    return settings


def save_settings(path: Path, settings: Settings) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    except OSError:
        pass  # Preferences are a convenience; never fail a request over them.


def _picker_command(mode: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, mode]
    return [sys.executable, "-m", "pywatermarkcleaner.web", mode]


def _run_picker(mode: str) -> list[str]:
    # tkinter runs in a child process so the dialog never blocks the server.
    try:
        result = subprocess.run(
            _picker_command(mode),
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=_NO_WINDOW,
        )
        paths = json.loads(result.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return [str(p) for p in paths] if isinstance(paths, list) else []


def pick_files() -> list[str]:
    return _run_picker("--pick-files")


def pick_folder() -> str | None:
    paths = _run_picker("--pick-folder")
    return paths[0] if paths else None


def run_tk_dialog(mode: str) -> list[str]:  # pragma: no cover - needs a display
    """Show the native dialog (used only inside the picker subprocess)."""
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if mode == "--pick-files":
            return list(
                filedialog.askopenfilenames(
                    title="Add videos", filetypes=[("Video files", VIDEO_PATTERNS)]
                )
            )
        chosen = filedialog.askdirectory(title="Choose output folder")
        return [chosen] if chosen else []
    finally:
        root.destroy()


def reveal_in_folder(path: Path) -> None:  # pragma: no cover - launches a file manager
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def build_proxy(source: Path, target: Path) -> None:
    """Transcode ``source`` to browser-playable H.264/AAC MP4 with a long edge <= 1280 px."""
    partial = target.with_suffix(".partial.mp4")
    scale = (
        "scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease"
        ":force_divisible_by=2"
    )
    command = [
        str(resolve_ffmpeg_executable()),
        "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-vf", scale,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart", str(partial),
    ]  # fmt: skip
    result = subprocess.run(command, capture_output=True, creationflags=_NO_WINDOW)
    if result.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        tail = result.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise MediaError(f"Could not build a playable proxy: {tail}")
    os.replace(partial, target)
