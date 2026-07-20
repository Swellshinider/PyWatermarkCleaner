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


def test_placeholder_entry_points_return_deliberate_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main() == 2
    assert gui.main() == 2

    captured = capsys.readouterr()
    assert "not implemented yet" in captured.err
