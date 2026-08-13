"""Logging.

Before this, only app/controllers/webhooks.py called logging.getLogger(), and nothing
ever called logging.basicConfig() -- so the root logger sat at Python's default WARNING
level and every logger.info() call in this app was silently dropped, never actually
reaching stdout. This makes that configuration explicit and controllable via LOG_LEVEL,
and mirrors everything shown in the terminal into logs/app.log too.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from config.settings import settings

# Public (no leading underscore): config/celery_app.py reuses these to route the
# Celery worker process's logging into logs/jobs.log instead of logs/app.log --
# see the after_setup_logger handler there for why that lives in a different file
# than this one.
LOG_DIR = "logs"
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_LOG_FILE = os.path.join(LOG_DIR, "app.log")
_LOG_MAX_BYTES = 50 * 1024 * 1024
_LOG_BACKUP_COUNT = 5


def configure_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = RotatingFileHandler(_LOG_FILE, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT)
    logging.basicConfig(
        level=settings.log_level,
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(), file_handler],
    )

    # Uvicorn configures "uvicorn"/"uvicorn.error"/"uvicorn.access" with their own
    # handler and propagate=False, so their records (startup/shutdown/reload, and
    # every access log line) never reach root and skip the app.log handler above.
    # Attach it directly so those lines land in app.log too, alongside whatever
    # Uvicorn still prints to the terminal via its own handler.
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(uvicorn_logger_name).addHandler(file_handler)
