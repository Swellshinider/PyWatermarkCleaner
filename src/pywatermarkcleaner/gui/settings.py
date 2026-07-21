"""Validated desktop preferences backed by QSettings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from pywatermarkcleaner.core.models import InpaintMethod, PerformanceMode
from pywatermarkcleaner.core.scheduler import max_allowed_workers


class AppSettings:
    def __init__(self, *, default_output: Path) -> None:
        self._settings = QSettings()
        self.worker_max = max_allowed_workers()
        stored_method = str(self._settings.value("method", InpaintMethod.TELEA.value))
        self.method = (
            stored_method
            if stored_method in {method.value for method in InpaintMethod}
            else InpaintMethod.TELEA.value
        )
        self.radius = self._bounded_int("radius", 3, 1, 10)
        stored_performance = str(
            self._settings.value("performance", PerformanceMode.BALANCED.value)
        )
        self.performance = (
            stored_performance
            if stored_performance in {mode.value for mode in PerformanceMode}
            else PerformanceMode.BALANCED.value
        )
        self.workers = self._bounded_int("workers", 1, 1, self.worker_max)
        stored_folder = self._settings.value("output_folder")
        self.output_folder = Path(stored_folder or default_output).expanduser().resolve()
        self.advanced_expanded = bool(self._settings.value("advanced_expanded", False, type=bool))

    def _bounded_int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(str(self._settings.value(key, default)))
        except (TypeError, ValueError):
            return default
        return value if minimum <= value <= maximum else default

    def set_method(self, value: str) -> bool:
        if value not in {method.value for method in InpaintMethod}:
            return False
        self.method = value
        self._settings.setValue("method", value)
        return True

    def set_radius(self, value: int) -> bool:
        if isinstance(value, bool) or not 1 <= value <= 10:
            return False
        self.radius = value
        self._settings.setValue("radius", value)
        return True

    def set_performance(self, value: str) -> bool:
        if value not in {mode.value for mode in PerformanceMode}:
            return False
        self.performance = value
        self._settings.setValue("performance", value)
        return True

    def set_workers(self, value: int) -> bool:
        if isinstance(value, bool) or not 1 <= value <= self.worker_max:
            return False
        self.workers = value
        self._settings.setValue("workers", value)
        return True

    def set_output_folder(self, value: Path) -> bool:
        text = str(value).strip()
        if not text:
            return False
        self.output_folder = Path(text).expanduser().resolve()
        self._settings.setValue("output_folder", str(self.output_folder))
        return True

    def set_advanced_expanded(self, expanded: bool) -> None:
        self.advanced_expanded = bool(expanded)
        self._settings.setValue("advanced_expanded", self.advanced_expanded)
