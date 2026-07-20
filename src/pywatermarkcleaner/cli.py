"""Command-line application boundary."""

import sys


def main() -> int:
    """Return a deliberate error until the desktop-v1 CLI is implemented."""
    print("pywatermarkcleaner: the desktop-v1 CLI is not implemented yet", file=sys.stderr)
    return 2
