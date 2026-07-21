from __future__ import annotations

import importlib.util
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from pywatermarkcleaner import cli

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_MAIN = PROJECT_ROOT / "main.py"


def test_legacy_main_imports_shared_entry_point_without_running(
    capsys: pytest.CaptureFixture[str],
) -> None:
    specification = importlib.util.spec_from_file_location("legacy_main_import", LEGACY_MAIN)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)

    specification.loader.exec_module(module)

    assert module.main is cli.main
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_legacy_main_delegates_when_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_main() -> int:
        nonlocal calls
        calls += 1
        return 17

    monkeypatch.setattr(cli, "main", fake_main)

    with pytest.raises(SystemExit) as exit_request:
        runpy.run_path(str(LEGACY_MAIN), run_name="__main__")

    assert exit_request.value.code == 17
    assert calls == 1


def test_legacy_main_version_matches_package_command() -> None:
    completed = subprocess.run(
        [sys.executable, str(LEGACY_MAIN), "--version"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == "pywatermarkcleaner 1.0.0\n"
    assert completed.stderr == ""


def test_duplicate_legacy_processing_modules_are_removed() -> None:
    assert not (PROJECT_ROOT / "coordinates.py").exists()
    assert not (PROJECT_ROOT / "video_converter.py").exists()
