"""Integration test: discover -> run round-trip — full filesystem pipeline.

Distinct angle from ``tests/test_registry_discovery.py``: that file is
``@pytest.mark.unit`` and asserts only *registry state* after ``discover``
(names registered, bundle ordering, hash-collision warnings). This file
exercises the *whole* pipeline end-to-end:

    YAML on disk -> discover() -> Registry -> orchestrator.run() -> typed model

Two distinct YAML specs are written into ``tmp_path/prompts/`` via
``yaml.safe_dump``. ``discover`` loads and compiles them into a fresh
``Registry``. Then ``run(name, text, backend=scripted, registry=reg)`` is
invoked per spec and the returned Pydantic instance is checked.

Adapter is scripted and dispatches by the schema's property set, so a single
adapter instance serves both specs without leaking state. Zero network,
zero API key.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Final, TypedDict

import pytest
import yaml
from pydantic import BaseModel

from prompiler.runtime.orchestrator import run
from prompiler.runtime.registry import Registry, discover


class _SpecField(TypedDict):
    name: str
    type: str
    required: bool


class _SpecDict(TypedDict):
    spec_version: int
    name: str
    task: str
    fields: list[_SpecField]


_INVOICE_SPEC: Final[_SpecDict] = {
    "spec_version": 1,
    "name": "invoice_line",
    "task": "extract",
    "fields": [
        {"name": "vendor", "type": "string", "required": True},
        {"name": "total", "type": "decimal", "required": True},
    ],
}

_RECEIPT_SPEC: Final[_SpecDict] = {
    "spec_version": 1,
    "name": "receipt_line",
    "task": "extract",
    "fields": [
        {"name": "merchant", "type": "string", "required": True},
        {"name": "amount", "type": "decimal", "required": True},
    ],
}

_INVOICE_PROPS: Final[frozenset[str]] = frozenset({"vendor", "total"})
_RECEIPT_PROPS: Final[frozenset[str]] = frozenset({"merchant", "amount"})

_INVOICE_PAYLOAD: Final[dict[str, Any]] = {
    "vendor": "Acme Corp",
    "total": "1234.56",
}

_RECEIPT_PAYLOAD: Final[dict[str, Any]] = {
    "merchant": "Corner Store",
    "amount": "9.99",
}


class _SchemaDispatchScriptedAdapter:
    """Scripted adapter dispatching by ``frozenset`` of schema properties.

    Identical shape to the heterogeneous-batch adapter — a single instance
    serves multiple specs without cross-talk because each spec has its own
    FIFO queue keyed on the property set.
    """

    def __init__(self, queues: dict[frozenset[str], list[dict[str, Any]]]) -> None:
        self._queues: dict[frozenset[str], list[dict[str, Any]]] = {
            key: list(items) for key, items in queues.items()
        }
        self.calls: int = 0

    async def extract(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        key = frozenset(json_schema["properties"].keys())
        return self._queues[key].pop(0)

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


def _write_spec(dir_path: Path, filename: str, spec: _SpecDict) -> None:
    (dir_path / filename).write_text(yaml.safe_dump(spec, sort_keys=False))


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


@pytest.mark.integration
def test_discover_then_run_returns_typed_instance_per_spec(
    tmp_path: Path,
) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    _write_spec(prompts_dir, "invoice.yaml", _INVOICE_SPEC)
    _write_spec(prompts_dir, "receipt.yaml", _RECEIPT_SPEC)

    registry = Registry()
    bundles = discover(prompts_dir, registry=registry)

    assert len(bundles) == 2
    assert set(registry.names()) == {"invoice_line", "receipt_line"}

    adapter = _SchemaDispatchScriptedAdapter(
        {
            _INVOICE_PROPS: [_INVOICE_PAYLOAD],
            _RECEIPT_PROPS: [_RECEIPT_PAYLOAD],
        }
    )

    invoice_result = asyncio.run(
        run("invoice_line", "vendor body", backend=adapter, registry=registry)
    )
    receipt_result = asyncio.run(
        run("receipt_line", "merchant body", backend=adapter, registry=registry)
    )

    assert isinstance(invoice_result, BaseModel)
    assert isinstance(receipt_result, BaseModel)
    assert type(invoice_result) is not type(receipt_result)
    assert _dump(invoice_result) == _INVOICE_PAYLOAD
    assert _dump(receipt_result) == _RECEIPT_PAYLOAD
    assert adapter.calls == 2


@pytest.mark.integration
def test_discover_then_concurrent_gather_returns_per_spec_instances(
    tmp_path: Path,
) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    _write_spec(prompts_dir, "invoice.yaml", _INVOICE_SPEC)
    _write_spec(prompts_dir, "receipt.yaml", _RECEIPT_SPEC)

    registry = Registry()
    discover(prompts_dir, registry=registry)

    adapter = _SchemaDispatchScriptedAdapter(
        {
            _INVOICE_PROPS: [_INVOICE_PAYLOAD],
            _RECEIPT_PROPS: [_RECEIPT_PAYLOAD],
        }
    )

    async def _gather() -> tuple[BaseModel, BaseModel]:
        return await asyncio.gather(
            run("invoice_line", "vendor body", backend=adapter, registry=registry),
            run(
                "receipt_line",
                "merchant body",
                backend=adapter,
                registry=registry,
            ),
        )

    invoice_result, receipt_result = asyncio.run(_gather())

    assert _dump(invoice_result) == _INVOICE_PAYLOAD
    assert _dump(receipt_result) == _RECEIPT_PAYLOAD
    assert adapter.calls == 2


@pytest.mark.integration
def test_discover_missing_dir_returns_empty_list_and_run_raises(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "does_not_exist"
    registry = Registry()
    bundles = discover(missing, registry=registry)

    assert bundles == []

    adapter = _SchemaDispatchScriptedAdapter({_INVOICE_PROPS: [_INVOICE_PAYLOAD]})

    with pytest.raises(KeyError):
        asyncio.run(run("invoice_line", "vendor body", backend=adapter, registry=registry))
