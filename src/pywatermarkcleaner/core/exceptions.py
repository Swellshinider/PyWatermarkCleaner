"""Actionable failures raised by Qt-free processing services."""


class MediaError(RuntimeError):
    """Raised when media cannot be opened, inspected, or decoded."""


class PreviewError(RuntimeError):
    """Raised when a requested preview cannot be rendered."""


class FFmpegNotFoundError(RuntimeError):
    """Raised when no usable FFmpeg executable can be resolved."""


class ExportError(RuntimeError):
    """Raised when a video export cannot be completed safely."""

