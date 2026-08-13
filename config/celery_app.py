import logging
import os

from celery import Celery
from celery.signals import after_setup_logger

from config.logging import LOG_DIR, LOG_FORMAT
from config.settings import settings

celery_app = Celery("devops_agent", broker=settings.celery_broker_url, include=["jobs.webhooks_job"])

_JOBS_LOG_FILE = os.path.join(LOG_DIR, "jobs.log")


@after_setup_logger.connect
def _route_worker_logs_to_jobs_log(logger, **kwargs) -> None:
    # Fires once, right after Celery hijacks the root logger on worker startup
    # (worker_hijack_root_logger defaults to True) -- `logger` here is Celery's own
    # replacement root logger, not the one config/logging.py set up. Every other
    # logger in this app propagates up to root by default, so attaching one handler
    # here captures everything that happens inside the worker process (task
    # lifecycle, RDS pipeline nodes, MCP tool calls, LLM HTTP calls) into jobs.log --
    # not just jobs/webhooks_job.py's own log lines. This only runs inside an actual
    # `celery worker` process, so app.log/the FastAPI process are unaffected.
    handler = logging.FileHandler(_JOBS_LOG_FILE)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
