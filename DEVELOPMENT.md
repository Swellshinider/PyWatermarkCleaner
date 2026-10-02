# Development guide

## Architecture

Production code lives under `src/pywatermarkcleaner`:

- `core/` is UI-free and owns immutable geometry, OpenCV media/inpainting, latest-only preview, FFmpeg export, cancellation, and scheduling.
- `cli.py` adapts those services to the current and legacy command-line interfaces.
- `web/` is a FastAPI server (localhost-only, token cookie auth) that adapts them to an HTTP/SSE API and serves the built frontend.
- `frontend/` is the Vite + React + TypeScript interface; `pnpm --dir frontend build` writes it to `src/pywatermarkcleaner/web/static/` (gitignored, required before packaging).
- `assets/` contains the SVG icon and bundled OFL fonts used by the desktop application.

Inputs and completed outputs are immutable from the application's perspective. Export writes a sibling partial file and publishes it through an atomic no-replace operation.

## Setup

```bash
uv sync --extra dev
```

Use Python 3.12-3.14. Install only `opencv-python`; mixing it with an OpenCV headless/contrib wheel in the same environment creates a shared `cv2` namespace conflict.

## Quality gates

```bash
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy src
uv run --locked coverage run -m pytest
uv run --locked coverage report --fail-under=85
```

Frontend checks (run from the repository root):

```bash
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend run typecheck
pnpm --dir frontend run lint
pnpm --dir frontend run test
pnpm --dir frontend run build
```

For live frontend work run `uv run pywatermarkcleaner-gui --no-browser` and `pnpm --dir frontend dev` (proxies `/api` to port 8765).

Run the deterministic server startup check with:

```bash
uv run --locked python -m pywatermarkcleaner.web --smoke-test
```

Integration tests generate tiny video-only and AAC samples through the `imageio-ffmpeg` executable; no binary media fixture is tracked.

## Packaging

Build the portable one-folder distribution on the target operating system:

```bash
uv run --locked pyinstaller --noconfirm --clean packaging/PyWatermarkCleaner.spec
```

The result under `dist/PyWatermarkCleaner` contains `PyWatermarkCleaner` (windowed launcher for the local web app), `PyWatermarkCleanerCLI` (console), shared libraries/assets, and the platform FFmpeg binary. PyInstaller does not cross-compile: build separately on Windows, Linux, macOS ARM64, and macOS Intel.

Smoke both launchers before archiving:

```text
Windows:
  dist\PyWatermarkCleaner\PyWatermarkCleanerCLI.exe --version
  dist\PyWatermarkCleaner\PyWatermarkCleaner.exe --smoke-test

macOS/Linux:
  dist/PyWatermarkCleaner/PyWatermarkCleanerCLI --version
  dist/PyWatermarkCleaner/PyWatermarkCleaner --smoke-test
```

The tag workflow builds unsigned portable archives for `v*` tags. It does not create a GitHub Release or sign/notarize artifacts.

## Contributions

Create a feature branch from an up-to-date `main`, write a failing test before production behavior, keep core UI-free, run every quality gate, and explain user-visible changes in the pull request. Do not commit generated videos, coverage/build folders, packaged applications, or `.superpowers` scratch files.
