"""
Logging configuration for Profoundd.
Sets up structured logging for both development and production.
"""
import os
import logging
import sys


def setup_logging(level=None):
    """Configure application-wide logging."""
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO").upper()

    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Root logger
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Quiet noisy loggers
    logging.getLogger("elasticsearch").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    logger = logging.getLogger("profoundd")
    logger.setLevel(getattr(logging, level, logging.INFO))

    return logger
