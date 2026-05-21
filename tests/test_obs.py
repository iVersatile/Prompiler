"""Unit tests for prompiler.obs (P0 L62).

Covers:
- ``JSONLinesFormatter`` emits one JSON object per record with the expected keys.
- ``redact_payload`` returns the value at TRACE and ``REDACTED`` above it.
- ``_resolve_level`` parses known names, defaults unknown/empty to INFO,
  and maps "warn"/"warning" to ``WARNING``.
- ``configure_logging`` is idempotent: repeated calls leave exactly one
  managed handler attached to the root logger.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from prompiler import obs as obs_module
from prompiler.obs import (
    REDACTED,
    TRACE,
    JSONLinesFormatter,
    configure_logging,
    redact_payload,
)


def _make_record(
    *,
    name: str = "prompiler.test",
    level: int = logging.INFO,
    msg: str = "hello",
    extra: dict[str, object] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


@pytest.mark.unit
def test_formatter_emits_required_fields() -> None:
    record = _make_record(extra={"event": "compile.start", "spec_hash": "abc123"})
    line = JSONLinesFormatter().format(record)
    payload = json.loads(line)
    assert payload["level"] == "info"
    assert payload["event"] == "compile.start"
    assert payload["msg"] == "hello"
    assert payload["spec_hash"] == "abc123"
    assert "ts" in payload and payload["ts"].endswith("+00:00")


@pytest.mark.unit
def test_formatter_defaults_event_to_logger_name() -> None:
    record = _make_record(name="prompiler.cli")
    payload = json.loads(JSONLinesFormatter().format(record))
    assert payload["event"] == "prompiler.cli"


@pytest.mark.unit
def test_formatter_strips_reserved_record_keys() -> None:
    record = _make_record()
    payload = json.loads(JSONLinesFormatter().format(record))
    for reserved in ("args", "levelname", "pathname", "lineno"):
        assert reserved not in payload


@pytest.mark.unit
def test_redact_payload_passes_value_at_trace() -> None:
    assert redact_payload("secret prompt", level=TRACE) == "secret prompt"


@pytest.mark.unit
@pytest.mark.parametrize(
    "level",
    [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL],
)
def test_redact_payload_redacts_above_trace(level: int) -> None:
    assert redact_payload("secret prompt", level=level) == REDACTED


@pytest.mark.unit
@pytest.mark.parametrize(
    "name,expected",
    [
        ("trace", TRACE),
        ("debug", logging.DEBUG),
        ("info", logging.INFO),
        ("warn", logging.WARNING),
        ("warning", logging.WARNING),
        ("error", logging.ERROR),
        ("critical", logging.CRITICAL),
        ("  INFO  ", logging.INFO),
    ],
)
def test_resolve_level_known_names(name: str, expected: int) -> None:
    assert obs_module._resolve_level(name) == expected


@pytest.mark.unit
@pytest.mark.parametrize("name", [None, "", "nonsense"])
def test_resolve_level_unknown_defaults_to_info(name: str | None) -> None:
    assert obs_module._resolve_level(name) == logging.INFO


@pytest.mark.unit
def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        for handler in list(root.handlers):
            root.removeHandler(handler)

        configure_logging("info")
        configure_logging("debug")
        configure_logging()

        managed = [h for h in root.handlers if getattr(h, "_prompiler_managed", False)]
        assert len(managed) == 1
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)


@pytest.mark.unit
def test_configure_logging_writes_jsonlines_to_stderr() -> None:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        for handler in list(root.handlers):
            root.removeHandler(handler)

        configure_logging("info")
        managed = next(h for h in root.handlers if getattr(h, "_prompiler_managed", False))
        buffer = io.StringIO()
        managed.stream = buffer  # type: ignore[attr-defined]

        logging.getLogger("prompiler.test").info("ping", extra={"event": "ping"})

        line = buffer.getvalue().strip()
        payload = json.loads(line)
        assert payload["event"] == "ping"
        assert payload["msg"] == "ping"
        assert payload["level"] == "info"
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)
