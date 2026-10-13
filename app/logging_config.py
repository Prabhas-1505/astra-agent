"""Shared logging setup for all ASTRA entry points (CLI, Gradio UI, syslog
listener, eval harness). Level is configurable via ASTRA_LOG_LEVEL so a
diagnosis run can be made verbose (DEBUG, to see every KB retrieval score)
or quiet (WARNING) without code changes.
"""

import logging
import os


def configure_logging() -> None:
    level_name = os.environ.get("ASTRA_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
