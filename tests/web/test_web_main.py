from __future__ import annotations

from pywatermarkcleaner.web import main


def test_smoke_test_starts_server_and_checks_health() -> None:
    assert main(["--smoke-test"]) == 0
