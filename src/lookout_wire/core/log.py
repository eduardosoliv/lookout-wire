"""Logging to stdout. Request-level HTTP logs are silenced so URLs never reach the log."""

import logging
import sys

QUIET_LOGGERS = ("httpx", "httpcore", "google_genai")


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
