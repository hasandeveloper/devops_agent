from config.redis_client import get_redis_client


def is_rate_limited(key: str, *, limit: int, window_seconds: int) -> bool:
    """Fixed-window rate limit: at most `limit` calls per `window_seconds`, keyed by `key`.

    A single INCR+EXPIRE pair, not a sliding-window algorithm -- simple, and Redis's
    INCR is atomic so concurrent requests can't race past the limit. The tradeoff is
    that a burst can land right at a window boundary and briefly allow up to ~2x limit
    in quick succession; acceptable here since this is a cost circuit-breaker against
    obviously-abusive traffic, not a precise quota system. The real throttle on actual
    diagnosis cost is the Celery task rate_limit in config/celery_app.py, which applies
    evenly over time rather than per fixed window.
    """
    client = get_redis_client()
    current = client.incr(key)
    if current == 1:
        client.expire(key, window_seconds)
    return current > limit
