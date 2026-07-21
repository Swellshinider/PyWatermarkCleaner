from importlib.metadata import distribution

import pytest

import pywatermarkcleaner
from pywatermarkcleaner import cli, core, gui


def test_package_version_matches_distribution_metadata() -> None:
    assert pywatermarkcleaner.__version__ == "1.0.0"
    assert distribution("pywatermarkcleaner").version == pywatermarkcleaner.__version__


def test_package_boundaries_are_importable() -> None:
    assert core.__doc__
    assert callable(cli.main)
    assert callable(gui.main)


def test_cli_placeholder_entry_point_returns_deliberate_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main() == 2

    captured = capsys.readouterr()
    assert "required" in captured.err
