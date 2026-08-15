"""Runs against the real local Redis (the same instance Celery's broker uses) --
it's fast, already required infra for this project, and INCR/EXPIRE behavior isn't
worth mocking.
"""

import uuid

from app.services.rate_limiter import is_rate_limited
from config.redis_client import get_redis_client


def _unique_key() -> str:
    # A fresh key per test, not shared/cleaned-up state -- avoids tests interfering
    # with each other or leaving counters behind in a real, shared Redis instance.
    return f"rate_limit:test:{uuid.uuid4()}"


def test_allows_requests_under_the_limit():
    key = _unique_key()
    results = [is_rate_limited(key, limit=3, window_seconds=5) for _ in range(3)]
    assert results == [False, False, False]


def test_blocks_requests_once_over_the_limit():
    key = _unique_key()
    results = [is_rate_limited(key, limit=3, window_seconds=5) for _ in range(5)]
    assert results == [False, False, False, True, True]


def test_resets_after_the_window_expires():
    key = _unique_key()
    assert is_rate_limited(key, limit=1, window_seconds=1) is False
    assert is_rate_limited(key, limit=1, window_seconds=1) is True

    get_redis_client().delete(key)  # simulate the window having expired, without a real sleep(1)

    assert is_rate_limited(key, limit=1, window_seconds=1) is False
