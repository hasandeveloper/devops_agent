"""Logging.

Before this, only app/controllers/webhooks.py called logging.getLogger(), and nothing
ever called logging.basicConfig() -- so the root logger sat at Python's default WARNING
level and every logger.info() call in this app was silently dropped, never actually
reaching stdout. This makes that configuration explicit and controllable via LOG_LEVEL,
and mirrors everything shown in the terminal into logs/app.log too.
"""

import logging
import os

from concurrent_log_handler import ConcurrentRotatingFileHandler

from config.settings import settings

# Public (no leading underscore): config/celery_app.py reuses these -- LOG_DIR/LOG_FORMAT
# to route the Celery worker process's logging into logs/jobs.log instead of
# logs/app.log (see the after_setup_logger handler there), and LOG_MAX_BYTES/
# LOG_BACKUP_COUNT so both files rotate on the same policy from one definition
# instead of two copies drifting out of sync.
LOG_DIR = "logs"
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
LOG_MAX_BYTES = 50 * 1024 * 1024
LOG_BACKUP_COUNT = 5
_LOG_FILE = os.path.join(LOG_DIR, "app.log")

_app_file_handler: ConcurrentRotatingFileHandler | None = None


def configure_logging() -> None:
    global _app_file_handler
    os.makedirs(LOG_DIR, exist_ok=True)
    # ConcurrentRotatingFileHandler, not stdlib's RotatingFileHandler -- it file-locks
    # around writes/rollovers, so rotation stays safe if this file is ever written by
    # more than one OS process (e.g. `uvicorn --workers N`). The stdlib handler has no
    # such lock: two processes rotating at once can corrupt the file or silently drop
    # lines, and independently-rotating handler instances can even keep writing into a
    # file that's already been renamed away by the other process's rotation.
    _app_file_handler = ConcurrentRotatingFileHandler(_LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    logging.basicConfig(
        level=settings.log_level,
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(), _app_file_handler],
    )


def attach_uvicorn_file_logging() -> None:
    """Make Uvicorn's own log lines land in app.log too.

    Call this from a FastAPI lifespan/startup hook, NOT at import time. Uvicorn
    configures "uvicorn"/"uvicorn.error"/"uvicorn.access" with their own handler and
    propagate=False via its own dictConfig call, so those records never reach root
    and skip the app.log handler configure_logging() sets up -- startup/shutdown/
    reload messages and every access log line would otherwise only ever hit the
    terminal, never the file. Attaching from a startup hook (guaranteed by the ASGI
    lifespan contract to run only after the server -- and therefore its own logging
    setup -- is fully up) makes this correct regardless of module import order,
    rather than depending on configure_logging() happening to run after Uvicorn
    configures its own loggers.
    """
    if _app_file_handler is None:
        raise RuntimeError("configure_logging() must run before attach_uvicorn_file_logging()")
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(uvicorn_logger_name).addHandler(_app_file_handler)
