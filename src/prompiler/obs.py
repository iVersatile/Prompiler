"""JSON-Lines structured logging for prompiler.

Emits one JSON object per log record on stderr. Honors the
``PROMPILER_LOG_LEVEL`` env var. Adds a ``TRACE`` level (numeric 5) below
``DEBUG``. Only ``TRACE`` may carry prompt or response payloads; lower
levels must redact via :func:`redact_payload`.

See ``docs/RULES.md`` §8 for the trace-gate policy.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any, Final

TRACE: Final[int] = 5
logging.addLevelName(TRACE, "TRACE")

_LEVEL_NAMES: Final[dict[str, int]] = {
    "trace": TRACE,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

_RESERVED_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "event",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }
)

REDACTED: Final[str] = "<redacted>"


class JSONLinesFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        out: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname.lower(),
            "event": getattr(record, "event", record.name),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_KEYS:
                continue
            out[key] = value
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False, sort_keys=False)


def redact_payload(value: Any, *, level: int) -> Any:
    """Return ``value`` only when ``level`` permits payload logging.

    Returns :data:`REDACTED` for any level above TRACE. Call this on any
    field that may carry prompt text or model response content before
    passing it to a logger.
    """
    if level <= TRACE:
        return value
    return REDACTED


def _resolve_level(name: str | None) -> int:
    if not name:
        return logging.INFO
    return _LEVEL_NAMES.get(name.strip().lower(), logging.INFO)


def configure_logging(level: str | None = None) -> None:
    """Install a JSON-Lines stderr handler on the root logger.

    ``level`` overrides ``PROMPILER_LOG_LEVEL``. Idempotent: removes any
    handler this function previously attached before re-attaching.
    """
    effective = level if level is not None else os.environ.get("PROMPILER_LOG_LEVEL")
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_prompiler_managed", False):
            root.removeHandler(handler)
    new_handler = logging.StreamHandler(stream=sys.stderr)
    new_handler.setFormatter(JSONLinesFormatter())
    new_handler._prompiler_managed = True  # type: ignore[attr-defined]
    root.addHandler(new_handler)
    root.setLevel(_resolve_level(effective))


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger. Use ``extra={"event": "..."}`` for routing."""
    return logging.getLogger(name)


__all__ = [
    "REDACTED",
    "TRACE",
    "JSONLinesFormatter",
    "configure_logging",
    "get_logger",
    "redact_payload",
]
