#!/usr/bin/env bash
#
# tutorial.sh — asciinema-driven walkthrough of docs/TUTORIAL.md.
#
# Mirrors the tutorial section-for-section so the recorded cast and the written
# tutorial never drift. Runs entirely on a CPU-only host with no API keys: the
# §5 extraction step uses a built-in scripted backend, so the cast is hermetic
# and reproducible.
#
# Record:
#   asciinema rec docs/demo/tutorial.cast \
#     --title "prompiler — first spec in under 15 minutes" \
#     --idle-time-limit 2 \
#     --command "bash docs/demo/tutorial.sh"
#
# Replay locally before committing:
#   asciinema play docs/demo/tutorial.cast
#
# Convert to GIF for the README (optional):
#   agg docs/demo/tutorial.cast docs/demo/tutorial.gif
#
set -euo pipefail

# --- presentation helpers ---------------------------------------------------
# `type_cmd` echoes a command as if typed, pauses, then runs it. `say` prints a
# narration line. Keep pauses short so the whole cast lands well under a minute
# of real time — the "15 minutes" claim is about the human task, not the cast.

PROMPT="\033[1;32m$\033[0m "
PAUSE="${DEMO_PAUSE:-1.2}"

say()  { printf "\033[2m# %s\033[0m\n" "$*"; sleep "$PAUSE"; }
type_cmd() { printf "%b%s\n" "$PROMPT" "$*"; sleep "$PAUSE"; eval "$*"; sleep "$PAUSE"; }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

# --- §1 Install -------------------------------------------------------------
say "1. Install — verify prompiler is on PATH."
type_cmd "prompiler --version"
type_cmd "prompiler --help"

# --- §2 Write a spec --------------------------------------------------------
say "2. Write a spec — a single YAML file. Every field needs a description."
cat > invoice.yaml <<'YAML'
spec_version: 2
name: invoice
task: extract
description: Extract billing details from a single invoice document.
fields:
  - name: vendor_name
    type: string
    required: true
    description: Legal name of the issuing vendor.
  - name: total_amount
    type: decimal
    required: true
    description: Grand total on the invoice in vendor currency.
YAML
type_cmd "cat invoice.yaml"

# --- §3 Validate ------------------------------------------------------------
say "3. Validate — loads the spec and runs the linter. Exit 0 means lint-clean."
type_cmd "prompiler validate invoice.yaml"
type_cmd "echo \"exit code: \$?\""

# --- §4 Generate artefacts --------------------------------------------------
say "4. Generate — deterministic prompt, Pydantic model, per-backend schema."
type_cmd "prompiler codegen invoice.yaml -o .prompiler/compiled"
type_cmd "find .prompiler/compiled -type f | sort"

# --- §5 Use it from Python --------------------------------------------------
say "5. Use it from Python — compile the bundle and inspect it."
cat > demo.py <<'PY'
from prompiler import compile
from prompiler.spec import load_spec

spec = load_spec("invoice.yaml")
bundle = compile(spec)

print("spec_hash:", bundle.spec_hash)
print("prompt (first line):", bundle.prompt.splitlines()[0])
print("model:", bundle.pydantic_cls.__name__)
PY
type_cmd "python demo.py"

say "Run a real extraction with run_sync. Here a scripted backend stands in for a live vendor — no API key, fully reproducible."
cat > extract.py <<'PY'
from typing import Any

from prompiler import run_sync
from prompiler.runtime.registry import register_from_path


# A BackendAdapter is any object with an async `extract` and a `to_tool_schema`.
# This one replays a fixed list of payloads so the demo is hermetic — swap for
# an Anthropic/OpenAI/Gemini/Ollama adapter in real use.
class ScriptedBackend:
    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)

    async def extract(
        self, *, prompt: str, json_schema: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        return self._payloads.pop(0)

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


# run_sync resolves "invoice" from the registry, so register the spec first.
register_from_path("invoice.yaml")

document = "Invoice from Acme Corp. Total due: $1,240.00"

# Happy path: the backend returns a valid payload on the first call.
backend = ScriptedBackend({"vendor_name": "Acme Corp", "total_amount": "1240.00"})
result = run_sync("invoice", document, backend=backend)
print("vendor:", result.vendor_name)
print("total :", result.total_amount)
PY
type_cmd "python extract.py"

say "The orchestrator self-heals one bad response. Here the backend returns an invalid payload first; prompiler feeds the validation error back and the retry succeeds."
cat > retry.py <<'PY'
from typing import Any

from prompiler import run_sync
from prompiler.runtime import ExtractionFailed
from prompiler.runtime.registry import register_from_path


class ScriptedBackend:
    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)
        self.calls = 0

    async def extract(
        self, *, prompt: str, json_schema: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        return self._payloads.pop(0)

    def to_tool_schema(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        return dict(json_schema)


register_from_path("invoice.yaml")

document = "Invoice from Acme Corp. Total due: $1,240.00"

# First payload omits the required total_amount -> ValidationError -> one
# corrective retry with the error fed back. The second payload validates.
backend = ScriptedBackend(
    {"vendor_name": "Acme Corp"},
    {"vendor_name": "Acme Corp", "total_amount": "1240.00"},
)
try:
    result = run_sync("invoice", document, backend=backend)
    print(f"recovered after {backend.calls} calls -> total: {result.total_amount}")
except ExtractionFailed as exc:
    print("extraction failed after one corrective retry:", exc)
PY
type_cmd "python retry.py"

# --- close ------------------------------------------------------------------
say "Done — install to validated extraction in one loop. Next: examples/, API.md, CLI.md."
sleep "$PAUSE"
