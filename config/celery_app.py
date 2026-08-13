import logging
import os

from celery import Celery
from celery.signals import after_setup_logger
from concurrent_log_handler import ConcurrentRotatingFileHandler

from config.logging import LOG_BACKUP_COUNT, LOG_DIR, LOG_FORMAT, LOG_MAX_BYTES
from config.settings import settings

celery_app = Celery("devops_agent", broker=settings.celery_broker_url, include=["jobs.webhooks_job"])

_JOBS_LOG_FILE = os.path.join(LOG_DIR, "jobs.log")
_jobs_log_attached = False


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
    #
    # Guarded by _jobs_log_attached: if this module ever ends up imported twice under
    # two different module identities (e.g. relative vs. absolute import paths), the
    # signal would otherwise be connected twice and every line would be written to
    # jobs.log twice.
    global _jobs_log_attached
    if _jobs_log_attached:
        return
    handler = ConcurrentRotatingFileHandler(_JOBS_LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    _jobs_log_attached = True
