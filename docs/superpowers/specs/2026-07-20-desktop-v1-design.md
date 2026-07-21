# PyWatermarkCleaner Desktop v1 Design

## Product goal

Turn the existing video-watermark CLI into a cross-platform desktop workbench for everyday creators while retaining a scriptable CLI. The application must make selecting a watermark, previewing the repair, and safely exporting several videos understandable without exposing codec details in the main workflow.

## Experience

The PySide6 Qt Widgets window uses three areas: a video queue, a dominant repair canvas, and an inspector. The empty canvas accepts dropped videos. Each valid video stores its own normalized rectangular region; **Apply to all** copies that normalized geometry to every loaded video. A valid region can be drawn, moved, resized, nudged with arrow keys, or edited numerically.

The signature interaction is the **repair aperture**: the current cleaned result appears inside the selected rectangle, while holding Space reveals the original. Scrubbing and silent playback request optimized frames no larger than 1280 pixels on their long edge. The preview worker keeps only the newest request, so slow processing drops preview frames instead of blocking input. Export always uses full-resolution frames.

The visual system uses marine panels (`#101A24`, `#172430`, `#263847`), frost text (`#E7F0F4`), muted cyan (`#59B7C8`) for ready/selection, and amber (`#E6A85C`) for pending/warnings. Barlow Semi Condensed, Atkinson Hyperlegible, and IBM Plex Mono serve headings, controls, and timecode when bundled fonts are available, with cross-platform system fallbacks. Motion is restrained and reduced-motion preferences are respected.

## Architecture and behavior

The installable `pywatermarkcleaner` package has a Qt-free core shared by GUI and CLI. Immutable normalized regions replace the mutable coordinate object. Core services cover metadata, frame reading, inpainting, output naming, FFmpeg export, cancellation, and bounded scheduling. GUI controllers adapt core events to queued Qt signals.

Telea with radius 3 is the default; Navier-Stokes and radius 1-10 are advanced options. The GUI defaults to one export worker and permits 1 through `min(4, logical CPU count)`. Queue states are Needs region, Ready, Queued, Processing, Completed, Failed, and Canceled. Export remains disabled until all accepted videos have valid regions.

FFmpeg receives full-resolution cleaned BGR frames through stdin, maps optional source audio, copies metadata, writes a temporary sibling file, and atomically renames it. MP4/MOV/M4V/MKV use H.264 CRF 18, AVI uses MPEG-4 quality 3, and WebM uses VP9 CRF 30. Unsupported input containers visibly fall back to MP4. Inputs are never overwritten; outputs use `<stem>_cleaned.<ext>` with numeric suffixes on collision. Partial files are removed on cancellation or failure.

The current CLI arguments remain valid, including negative coordinates and `--thread`; new options select output directory, method, radius, and workers. An unchanged legacy invocation adopts the desktop-safe output directory and same-container naming. Cancellation exits with status 130; other failures return nonzero status.

## Quality and release

Support Python 3.12-3.14. Use Ruff, mypy, pytest, pytest-qt, and at least 85% core coverage. Test geometry, preview staleness, inpainting, export with and without audio, collisions, cancellation, queue state, GUI interactions, and CLI compatibility with generated tiny videos.

CI targets Ubuntu 24.04, Windows 2025, macOS 15 ARM, and macOS 15 Intel. A `v1.0.0` tag creates unsigned PyInstaller one-folder artifacts with the platform FFmpeg executable. Documentation covers GUI and CLI workflows, migration, troubleshooting, third-party notices, and contribution.

Signing, notarization, installers, auto-update, AI removal, tracking/keyframes, and multiple simultaneous regions are deferred.
