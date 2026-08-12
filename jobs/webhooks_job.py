import asyncio
import logging
import uuid

from app.agents import supervisor
from config.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def aws_sns_event_job(self, raw_event: dict) -> None:
    raw_event = {**raw_event, "id": uuid.UUID(raw_event["id"])}
    try:
        asyncio.run(supervisor.route(raw_event))
    except (NotImplementedError, ValueError) as exc:
        # A domain agent that genuinely doesn't exist yet (ECS/ALB/CI-CD) or an
        # alarm namespace we don't recognize -- retrying can never succeed, so don't.
        logger.warning("no domain agent for raw_event_id=%s: %s", raw_event["id"], exc)
    except Exception as exc:
        logger.exception("agent routing failed for raw_event_id=%s, retrying", raw_event["id"])
        raise self.retry(exc=exc)
