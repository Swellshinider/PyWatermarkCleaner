"""Desktop application boundary."""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    from .app import main as application_main

    return application_main(argv)
