"""Compatibility entry point for legacy ``python main.py`` invocations."""

from pywatermarkcleaner.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
