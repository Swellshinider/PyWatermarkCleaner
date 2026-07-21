"""Centralized precision-studio palette, typography, and asset loading."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

BACKGROUND = "#101A24"
PANEL = "#172430"
RAISED = "#263847"
TEXT = "#E7F0F4"
CYAN = "#59B7C8"
AMBER = "#E6A85C"

REQUIRED_FONT_ASSETS = (
    "BarlowSemiCondensed-SemiBold.ttf",
    "AtkinsonHyperlegible-Regular.ttf",
    "IBMPlexMono-Regular.ttf",
)
REQUIRED_FONT_LICENSES = (
    "OFL-BarlowSemiCondensed.txt",
    "OFL-AtkinsonHyperlegible.txt",
    "OFL-IBMPlexMono.txt",
)


def assets_directory() -> Path:
    return Path(__file__).resolve().parents[1] / "assets"


def install_theme(application: QApplication) -> None:
    fonts = assets_directory() / "fonts"
    if fonts.is_dir():
        for font_path in fonts.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font_path))
    # Qt's Windows offscreen plugin does not discover the system font directory.
    for fallback in (
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arialn.ttf"),
        Path("C:/Windows/Fonts/consola.ttf"),
    ):
        if fallback.is_file():
            QFontDatabase.addApplicationFont(str(fallback))
    application.setStyleSheet(
        f"""
        * {{ font-family: 'Atkinson Hyperlegible', 'Segoe UI', sans-serif; }}
        QWidget {{ background: {BACKGROUND}; color: {TEXT}; font-size: 13px; }}
        QMainWindow, QDialog {{ background: {BACKGROUND}; }}
        #queuePanel, #inspectorPanel, #batchBar {{ background: {PANEL}; }}
        QLabel#heading {{ font-family: 'Barlow Semi Condensed', 'Arial Narrow';
                         font-size: 18px; font-weight: 600; }}
        QLineEdit, QSpinBox, QComboBox, QListView, QTextEdit {{
            background: {RAISED}; border: 1px solid #395062; border-radius: 4px;
            padding: 6px; selection-background-color: {CYAN};
        }}
        QPushButton {{ background: {RAISED}; border: 1px solid #395062;
                       border-radius: 4px; padding: 7px 10px; }}
        QPushButton:hover {{ border-color: {CYAN}; }}
        QPushButton:focus, QLineEdit:focus, QSpinBox:focus, QComboBox:focus,
        QListView:focus, QSlider:focus {{ border: 2px solid {CYAN}; }}
        QPushButton#primary {{ background: {CYAN}; color: {BACKGROUND};
                              font-weight: 700; min-height: 44px; }}
        QPushButton#primary:disabled {{ background: #3B5662; color: #91A4AD; }}
        QProgressBar {{ border: 1px solid #395062; border-radius: 3px;
                        background: {BACKGROUND}; text-align: center; }}
        QProgressBar::chunk {{ background: {CYAN}; border-radius: 2px; }}
        QSlider::groove:horizontal {{ height: 4px; background: #395062; }}
        QSlider::handle:horizontal {{ width: 16px; margin: -7px 0;
                                     border-radius: 6px; background: {CYAN}; }}
        QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {CYAN}; }}
        """
    )


def app_icon() -> QIcon:
    return QIcon(str(assets_directory() / "icons" / "repair-aperture.svg"))


def validate_required_assets() -> None:
    assets = assets_directory()
    icon = assets / "icons" / "repair-aperture.svg"
    if not icon.is_file() or QIcon(str(icon)).isNull():
        raise RuntimeError(f"Required app icon is missing or invalid: {icon}")
    font_dir = assets / "fonts"
    for name in REQUIRED_FONT_ASSETS:
        font = font_dir / name
        if not font.is_file() or font.stat().st_size <= 100:
            raise RuntimeError(f"Required font asset is missing or empty: {font}")
        if QFontDatabase.addApplicationFont(str(font)) < 0:
            raise RuntimeError(f"Required font asset is invalid: {font}")
    for name in REQUIRED_FONT_LICENSES:
        license_path = font_dir / name
        try:
            license_text = license_path.read_text(encoding="utf-8")
        except OSError as error:
            raise RuntimeError(f"Required font license is missing: {license_path}") from error
        if "SIL OPEN FONT LICENSE" not in license_text.upper():
            raise RuntimeError(f"Required font license is invalid: {license_path}")
