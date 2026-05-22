"""Integration test: CLI must not log unredacted spec content at INFO.

Source of truth: docs/RULES.md §8 (no prompt/response payloads logged except
at PROMPILER_LOG_LEVEL=trace) and PRD §observability.

Spawns ``prompiler codegen`` as a subprocess with PROMPILER_LOG_LEVEL=info,
parses stderr JSON-Lines, and asserts no spec name/description/label value
appears verbatim in any log record. Empty stderr is a valid PASS at the
current SHA (codegen path emits no log records yet); the test acts as a
regression floor for P2.x backend instrumentation when log volume grows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "examples" / "email_category.yaml"

# Verbatim spec content drawn from examples/email_category.yaml. If those
# strings appear in any structured log record at INFO, redaction is broken.
FORBIDDEN_NEEDLES: tuple[str, ...] = (
    "email_category",
    "Route inbound support email into one routing bucket.",
    "Payments, invoices, refunds.",
    "Product not working.",
)


@pytest.mark.integration
def test_codegen_does_not_log_spec_at_info(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PROMPILER_LOG_LEVEL"] = "info"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "prompiler.cli",
            "codegen",
            str(SPEC_PATH),
            "-o",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, (
        f"codegen failed (rc={proc.returncode}); stdout={proc.stdout!r}; stderr={proc.stderr!r}"
    )

    for raw in proc.stderr.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"non-JSON line on stderr at INFO (logging contract broken): {line!r}")
        flat = json.dumps(record)
        for needle in FORBIDDEN_NEEDLES:
            assert needle not in flat, (
                f"unredacted spec content {needle!r} appeared in log record {record!r}"
            )
