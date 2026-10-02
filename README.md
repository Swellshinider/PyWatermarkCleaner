# PyWatermarkCleaner

PyWatermarkCleaner is a cross-platform local web application for removing a fixed watermark region from one or more videos with OpenCV inpainting. Drop in videos, draw the repair area, inspect a live optimized preview, and export full-resolution cleaned copies without overwriting the originals.

The web app runs on your machine (`127.0.0.1` only), opens in your browser, and never uploads your videos anywhere. A compatible CLI remains available for scripts and batch jobs.

## Highlights

- Drag-and-drop video queue with per-video repair regions
- Smooth native playback with audio, cleaned-frame preview when paused, and short cleaned preview clips
- Direct draw, move, resize, pixel editing, and keyboard nudging
- Telea and Navier-Stokes inpainting
- Configurable parallel exports with progress, retry, per-item cancel, and cancel-all
- Safe same-container output names with optional source audio and metadata
- Bundled FFmpeg through `imageio-ffmpeg`; no PATH setup required
- Windows, macOS, and Linux support on Python 3.12-3.14

## Install from source

Python 3.12, 3.13, or 3.14 is required.
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first.

```bash
git clone https://github.com/Swellshinider/PyWatermarkCleaner.git
cd PyWatermarkCleaner
uv sync
pnpm --dir frontend install && pnpm --dir frontend build
uv run pywatermarkcleaner-gui
```

The last command starts the local server and opens your browser. Closing the tab stops it after a short idle period.

Portable release archives include Python, the web interface, OpenCV, and FFmpeg. They are unsigned in v1.0.0, so Windows SmartScreen or macOS Gatekeeper may ask you to confirm the first launch.

## Workflow

1. Drop videos onto the page or choose **Add videos**.
2. Select a video and drag a rectangle over the watermark.
3. Move or resize the repair aperture. Arrow keys nudge one source pixel; Shift+arrow nudges ten.
4. Play the video normally; pause or scrub to see the cleaned frame, or render a short cleaned **Preview clip**. Hold Space to reveal the original region.
5. Configure Telea/Navier-Stokes, radius, Fast/Balanced/Quality, output folder, and worker count as needed.
6. Use **Apply to all** to copy the normalized region to videos with compatible dimensions.
7. Choose **Clean videos**. Completed outputs can be revealed from the queue.

Cleaned previews are downscaled to a maximum 1280-pixel long edge. Formats the browser cannot play (such as MKV or AVI) are played through a temporary H.264 proxy. Exports always process full-resolution frames.

Outputs default to the platform Videos/Movies folder under `PyWatermarkCleaner` and use `<name>_cleaned.<ext>`, then `_2`, `_3`, and so on. Inputs are never overwritten. MP4/MOV/M4V/MKV use the first working H.264 encoder among NVENC, QSV, AMF, VideoToolbox, and x264. AVI uses MPEG-4 and WebM uses VP9. A batch containing AVI or WebM can instead be converted to accelerated MP4; unsupported containers visibly fall back to MP4.

## Command line

The installed command and the legacy `python main.py` entry point are equivalent:

```bash
uv run pywatermarkcleaner \
  -i video.mp4 second.mov \
  --x 1280 --y 40 --width 420 --height 120 \
  --method telea --radius 3 --workers 2 \
  --performance balanced --format-policy original
```

Negative `--x` and `--y` count from the right and bottom of each input independently. Useful options:

```text
-i, --input PATH [PATH ...]     Input videos
--x/--y/--width/--height INT    Repair rectangle in source pixels
--thread, --workers INT         Concurrent jobs (1 through min(4, CPU count))
--method METHOD                 telea or navier-stokes
--radius INT                    Inpainting radius, 1 through 10
--performance MODE              fast, balanced, or quality (default: balanced)
--format-policy POLICY          original or mp4 (default: original)
--output-dir PATH               Destination folder
--preview                       Export only the first five seconds
--benchmark                     Report encoder FPS and real-time factor
--version                       Print the application version
```

Exit status is 0 for success, 1 when processing fails, 2 for invalid arguments/media, and 130 for cancellation. Unlike older releases, unchanged legacy invocations now use the safe platform output folder and same-container naming rather than `result_*.mp4` in the current directory.

## Troubleshooting

- **A video will not enter the queue:** it could not be opened or has no readable video stream. Convert or repair it with a media tool and try again.
- **Clean videos is disabled:** every queued item needs a valid region at least 2×2 source pixels.
- **Export codec is unavailable:** use a release archive or reinstall `imageio-ffmpeg`; custom FFmpeg builds must contain libx264/libvpx support.
- **Output folder is not writable/full:** choose another folder, then retry the failed row.
- **The browser does not open:** copy the `http://127.0.0.1:<port>/?token=...` address printed in the terminal.
- **File picker does not appear (Linux):** install Tk support (for example `python3-tk`) or drag files onto the page.

The **Activity log** and **Copy diagnostics** actions provide sanitized versions and job details without copying video frames.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for architecture, tests, CI, packaging, and contribution commands. Third-party software and font licenses are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Known limitations

Tracked for follow-up work:

- **Slow first play of MKV/AVI:** formats the browser cannot decode are transcoded to a temporary H.264 proxy. The whole file is converted on the first request, so long videos wait before playback starts. Fix idea: transcode in the background with progress, or stream segments.
- **Output name collisions:** output paths are allocated when a job is submitted, so two queued files with the same stem can get the same `<name>_cleaned.<ext>`. Fix idea: reserve the name at allocation time.
- **Untested paths:** preview clips from files with audio, the native file/folder dialogs (tkinter), and **Show in folder** have no automated coverage and need manual checks on each platform.
- **Not yet verified in a real browser:** playback with audio, the cleaned-frame preview while paused, live export progress, and touch input.

## Deferred after v1

Code signing/notarization, native installers, automatic updates, AI-based removal, moving-watermark tracking/keyframes, and multiple simultaneous repair regions are intentionally deferred.

## License

PyWatermarkCleaner is released under the [MIT License](LICENSE).
