# Third-party notices

PyWatermarkCleaner itself is licensed under MIT. Runtime distributions include or depend on the following projects; their own license terms continue to apply.

| Component | Purpose | License / upstream |
|---|---|---|
| NumPy | Frame arrays | BSD-3-Clause — https://numpy.org/ |
| OpenCV / opencv-python | Decode, seek, inpainting | Apache-2.0 / MIT packaging — https://opencv.org/ and https://github.com/opencv/opencv-python |
| Qt / PySide6 | Desktop interface | LGPL-3.0/GPL-3.0/commercial options — https://www.qt.io/qt-for-python |
| imageio-ffmpeg | FFmpeg discovery and platform wheels | BSD-2-Clause — https://github.com/imageio/imageio-ffmpeg |
| FFmpeg | Video encoding and audio/metadata mapping | Primarily LGPL-2.1-or-later; optional compiled components can make a binary GPL-2.0-or-later — https://ffmpeg.org/legal.html |
| PyInstaller | Portable application bundle | GPL-2.0-or-later with bootloader exception — https://pyinstaller.org/ |

The exact FFmpeg binary license depends on the configuration and optional codecs used by the binary shipped in the selected `imageio-ffmpeg` wheel. Distributors must inspect `ffmpeg -buildconf`, retain the applicable notices, and comply with any source-offer requirements before redistribution.

## Bundled fonts

The following unmodified font files are bundled under the SIL Open Font License 1.1:

- Barlow Semi Condensed SemiBold — https://github.com/google/fonts/tree/main/ofl/barlowsemicondensed
- Atkinson Hyperlegible Regular — https://github.com/google/fonts/tree/main/ofl/atkinsonhyperlegible
- IBM Plex Mono Regular — https://github.com/google/fonts/tree/main/ofl/ibmplexmono

Their full OFL texts are stored beside the font files under `src/pywatermarkcleaner/assets/fonts/`.

OpenCV wheels may also bundle FFmpeg and other native libraries. See the wheel's `LICENSE-3RD-PARTY.txt` for the exact build installed or redistributed.
