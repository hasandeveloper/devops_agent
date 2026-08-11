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

_LOG_DIR = "logs"
_LOG_FILE = os.path.join(_LOG_DIR, "app.log")



def configure_logging() -> None:
    os.makedirs(_LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(_LOG_FILE)],
    )
