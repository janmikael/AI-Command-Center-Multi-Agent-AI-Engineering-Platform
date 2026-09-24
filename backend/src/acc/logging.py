"""Structured logging. Everything (including uvicorn and SQLAlchemy) goes through structlog."""

import logging
import re
import sys

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

_SENSITIVE_KEY = re.compile(r"pass(word)?|secret|token|authorization|cookie|api[_-]?key", re.I)
REDACTED = "[REDACTED]"


def redact_sensitive(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Mask values whose key looks like a credential (defense in depth; still never log them)."""
    for key in list(event_dict):
        if key != "event" and _SENSITIVE_KEY.search(key):
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
    ]
    final: list[Processor] = (
        [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
        if json
        else [structlog.dev.ConsoleRenderer()]
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *final],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Route uvicorn through the root handler; its access log is replaced by our middleware.
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
