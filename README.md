# PyWatermarkCleaner

PyWatermarkCleaner is a cross-platform desktop application for removing a fixed watermark region from one or more videos with OpenCV inpainting. Drop in videos, draw the repair area, inspect a live optimized preview, and export full-resolution cleaned copies without overwriting the originals.

The desktop app is the primary experience. A compatible CLI remains available for scripts and batch jobs.

## Highlights

- Drag-and-drop video queue with per-video repair regions
- Responsive silent preview while scrubbing or playing
- Direct draw, move, resize, pixel editing, and keyboard nudging
- Telea and Navier-Stokes inpainting
- Configurable parallel exports with progress, retry, per-item cancel, and cancel-all
- Safe same-container output names with optional source audio and metadata
- Bundled FFmpeg through `imageio-ffmpeg`; no PATH setup required
- Windows, macOS, and Linux support on Python 3.12-3.14

## Install from source

Python 3.12, 3.13, or 3.14 is required.

```bash
git clone https://github.com/Swellshinider/PyWatermarkCleaner.git
cd PyWatermarkCleaner
python -m venv .venv
```

Activate the environment:

```text
Windows:  .venv\Scripts\activate
macOS/Linux: source .venv/bin/activate
```

Install and launch:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
pywatermarkcleaner-gui
```

Portable release archives include Python, Qt, OpenCV, and FFmpeg. They are unsigned in v1.0.0, so Windows SmartScreen or macOS Gatekeeper may ask you to confirm the first launch.

## Desktop workflow

1. Drop videos onto the window or choose **Add videos**.
2. Select a video and drag a rectangle over the watermark.
3. Move or resize the repair aperture. Arrow keys nudge one source pixel; Shift+arrow nudges ten.
4. Scrub or play the silent optimized preview. Hold Space to reveal the original region.
5. Configure Telea/Navier-Stokes, radius, output folder, and worker count as needed.
6. Use **Apply to all** to copy the normalized region to videos with compatible dimensions.
7. Choose **Clean videos**. Completed outputs can be revealed from the queue.

The preview is downscaled to a maximum 1280-pixel long edge to remain responsive and may drop visual frames when processing falls behind. Exports always process full-resolution frames.

Outputs default to the platform Videos/Movies folder under `PyWatermarkCleaner` and use `<name>_cleaned.<ext>`, then `_2`, `_3`, and so on. Inputs are never overwritten. MP4/MOV/M4V/MKV use H.264, AVI uses MPEG-4, and WebM uses VP9; unsupported containers visibly fall back to MP4.

## Command line

The installed command and the legacy `python main.py` entry point are equivalent:

```bash
pywatermarkcleaner \
  -i video.mp4 second.mov \
  --x 1280 --y 40 --width 420 --height 120 \
  --method telea --radius 3 --workers 2
```

Negative `--x` and `--y` count from the right and bottom of each input independently. Useful options:

```text
-i, --input PATH [PATH ...]     Input videos
--x/--y/--width/--height INT    Repair rectangle in source pixels
--thread, --workers INT         Concurrent jobs (1 through min(4, CPU count))
--method METHOD                 telea or navier-stokes
--radius INT                    Inpainting radius, 1 through 10
--output-dir PATH               Destination folder
--preview                       Export only the first five seconds
--version                       Print the application version
```

Exit status is 0 for success, 1 when processing fails, 2 for invalid arguments/media, and 130 for cancellation. Unlike older releases, unchanged legacy invocations now use the safe platform output folder and same-container naming rather than `result_*.mp4` in the current directory.

## Troubleshooting

- **A video will not enter the queue:** it could not be opened or has no readable video stream. Convert or repair it with a media tool and try again.
- **Clean videos is disabled:** every queued item needs a valid region at least 2×2 source pixels.
- **Export codec is unavailable:** use a release archive or reinstall `imageio-ffmpeg`; custom FFmpeg builds must contain libx264/libvpx support.
- **Output folder is not writable/full:** choose another folder, then retry the failed row.
- **Linux window does not open:** install the platform libraries required by Qt/OpenCV (OpenGL, EGL, XKB) for your distribution.

The **Activity log** and **Copy diagnostics** actions provide sanitized versions and job details without copying video frames.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for architecture, tests, CI, packaging, and contribution commands. Third-party software and font licenses are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Deferred after v1

Code signing/notarization, native installers, automatic updates, AI-based removal, moving-watermark tracking/keyframes, and multiple simultaneous repair regions are intentionally deferred.

## License

PyWatermarkCleaner is released under the [MIT License](LICENSE).
