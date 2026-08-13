"""Logging.

Before this, only app/controllers/webhooks.py called logging.getLogger(), and nothing
ever called logging.basicConfig() -- so the root logger sat at Python's default WARNING
level and every logger.info() call in this app was silently dropped, never actually
reaching stdout. This makes that configuration explicit and controllable via LOG_LEVEL,
and mirrors everything shown in the terminal into logs/app.log too.
"""

import logging
import os

from config.settings import settings

# Public (no leading underscore): config/celery_app.py reuses these to route the
# Celery worker process's logging into logs/jobs.log instead of logs/app.log --
# see the after_setup_logger handler there for why that lives in a different file
# than this one.
LOG_DIR = "logs"
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_LOG_FILE = os.path.join(LOG_DIR, "app.log")


def configure_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=settings.log_level,
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(), logging.FileHandler(_LOG_FILE)],
    )
