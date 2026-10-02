# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import imageio_ffmpeg
from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPEC).resolve().parents[1]
SOURCE = ROOT / "src"
PACKAGE = SOURCE / "pywatermarkcleaner"
FFMPEG = Path(imageio_ffmpeg.get_ffmpeg_exe())

STATIC = PACKAGE / "web" / "static"
if not (STATIC / "index.html").is_file():
    raise SystemExit("Build the frontend first: pnpm --dir frontend install && pnpm --dir frontend build")

datas = [
    (str(PACKAGE / "assets"), "pywatermarkcleaner/assets"),
    (str(STATIC), "pywatermarkcleaner/web/static"),
]
binaries = [
    (str(FFMPEG), "imageio_ffmpeg/binaries"),
]

common = dict(
    pathex=[str(SOURCE), str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("uvicorn"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)

gui_analysis = Analysis([str(ROOT / "packaging" / "pywatermarkcleaner_gui.py")], **common)
cli_analysis = Analysis([str(ROOT / "packaging" / "pywatermarkcleaner_cli.py")], **common)
MERGE((gui_analysis, "gui", "gui"), (cli_analysis, "cli", "cli"))

gui_pyz = PYZ(gui_analysis.pure)
cli_pyz = PYZ(cli_analysis.pure)

gui = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="PyWatermarkCleaner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

cli = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="PyWatermarkCleanerCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

bundle = COLLECT(
    gui,
    cli,
    gui_analysis.binaries,
    gui_analysis.datas,
    cli_analysis.binaries,
    cli_analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PyWatermarkCleaner",
)
