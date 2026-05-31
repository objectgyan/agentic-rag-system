"""Tests for the document-processing retry backoff (F13)."""

from app.services.processing.tasks import (
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    _retry_countdown,
)


def test_backoff_within_jitter_band_and_grows():
    # For each retry count the delay is in [window/2, window] (equal jitter),
    # where window doubles each attempt up to the cap.
    for retries in range(6):
        window = min(RETRY_MAX_DELAY_SECONDS, RETRY_BASE_DELAY_SECONDS * (2 ** retries))
        delay = _retry_countdown(retries)
        assert window / 2 - 1 <= delay <= window


def test_backoff_is_capped():
    # A large retry count must not exceed the cap.
    assert _retry_countdown(100) <= RETRY_MAX_DELAY_SECONDS


def test_backoff_first_attempt_is_short():
    # The very first retry should be on the order of the base delay, not minutes.
    assert _retry_countdown(0) <= RETRY_BASE_DELAY_SECONDS
