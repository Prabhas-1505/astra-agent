"""Shared logging setup for all ASTRA entry points (CLI, Gradio UI, syslog
listener, eval harness). Level is configurable via ASTRA_LOG_LEVEL so a
diagnosis run can be made verbose (DEBUG, to see every KB retrieval score) or
quiet (WARNING) without code changes. ASTRA_LOG_FORMAT=json switches to
structured JSON lines for log aggregation; text (default) stays
human-readable for local runs.

A correlation ID (set once per diagnosis run via `new_correlation_id()`) is
attached to every log line through a contextvar + logging Filter, rather than
threading an explicit parameter through every node function — this is the
cheap version of distributed tracing: enough to grep one run's logs out of a
shared stream, without the OpenTelemetry/collector infrastructure that isn't
justified at this project's current scale (2 devices, single process).
"""

import contextvars
import json
import logging
import os
import uuid

_correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-"
)


def new_correlation_id() -> str:
    """Call once per diagnosis run (CLI/UI/listener/eval entry points, right
    before `graph.invoke(...)`) so every log line emitted during that run —
    across all nodes, KB lookups, and Netmiko calls — carries the same ID.
    """
    correlation_id = uuid.uuid4().hex[:12]
    _correlation_id_var.set(correlation_id)
    return correlation_id


def current_correlation_id() -> str:
    return _correlation_id_var.get()


class _CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    level_name = os.environ.get("ASTRA_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.addFilter(_CorrelationIdFilter())
    if os.environ.get("ASTRA_LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s [%(correlation_id)s]: %(message)s")
        )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
