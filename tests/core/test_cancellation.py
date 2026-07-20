import pytest

from pywatermarkcleaner.core.cancellation import CancellationToken, CancelledError


def test_token_is_not_cancelled_initially() -> None:
    token = CancellationToken()

    assert not token.is_cancelled()
    token.raise_if_cancelled()


def test_cancel_marks_token_and_raises_project_exception() -> None:
    token = CancellationToken()

    token.cancel()

    assert token.is_cancelled()
    with pytest.raises(CancelledError):
        token.raise_if_cancelled()
