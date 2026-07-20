"""Thread-safe cooperative cancellation primitives."""

from threading import Event


class CancelledError(RuntimeError):
    """Raised when cooperative processing observes cancellation."""


class CancellationToken:
    """A small wrapper around a thread event for worker APIs."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise CancelledError("processing was cancelled")
