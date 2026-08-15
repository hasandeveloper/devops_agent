import redis

from config.settings import settings

_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    """The single place anything outside Celery itself gets a Redis client from.

    Reuses the same Redis instance Celery's broker already points at
    (settings.celery_broker_url) -- this project doesn't run a separate Redis for
    anything else, no reason to pretend otherwise or ask for a second URL.
    """
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.celery_broker_url, decode_responses=True)
    return _client
