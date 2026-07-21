"""Qt application entry point and deterministic offscreen smoke mode."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from PySide6.QtWidgets import QApplication

from pywatermarkcleaner.core.export import resolve_ffmpeg_executable

from .theme import install_theme, validate_required_assets
from .window import MainWindow


def main(
    argv: Sequence[str] | None = None,
    *,
    ffmpeg_resolver: Callable[[], Path] = resolve_ffmpeg_executable,
    validate_assets: bool = True,
) -> int:
    parser = argparse.ArgumentParser(prog="pywatermarkcleaner-gui")
    parser.add_argument("--smoke-test", action="store_true")
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    existing = QApplication.instance()
    application = (
        cast(QApplication, existing) if existing is not None else QApplication(sys.argv[:1])
    )
    application.setApplicationName("PyWatermarkCleaner")
    application.setOrganizationName("PyWatermarkCleaner")
    install_theme(application)
    try:
        if validate_assets:
            validate_required_assets()
        ffmpeg_resolver()
        window = MainWindow()
        if arguments.smoke_test:
            window.show()
            application.processEvents()
            window.close()
            application.processEvents()
            print("PyWatermarkCleaner GUI smoke test: OK")
            return 0
        window.show()
        return application.exec()
    except Exception as error:
        print(f"PyWatermarkCleaner GUI startup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
