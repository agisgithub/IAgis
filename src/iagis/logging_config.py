"""Logs JSON com filtragem defensiva de segredos."""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

_SENSITIVE = re.compile(r"(token|authorization|password|secret|api[_-]?key|credential)", re.I)


def redact_secrets(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if _SENSITIVE.search(str(key)):
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            redact_secrets,
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
    )
