"""Logging.

Before this, only app/controllers/webhooks.py called logging.getLogger(), and nothing
ever called logging.basicConfig() -- so the root logger sat at Python's default WARNING
level and every logger.info() call in this app was silently dropped, never actually
reaching stdout. This makes that configuration explicit and controllable via LOG_LEVEL.
"""

import logging

from config.settings import settings


def configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
