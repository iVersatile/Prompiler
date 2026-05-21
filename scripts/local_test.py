#!/usr/bin/env python3
"""Local test gate for prompiler.

Source of truth: docs/RULES.md §9. Also referenced from §5 (version tag gate).
Pytest is deliberately excluded — covered by §2 (pre-commit).

Checks, in order:
  1. `uv run prompiler --help` exits 0 and lists compile/extract/validate/serve.
  2. `uv run prompiler compile <spec>` exits 0 for every example spec under
     `examples/`, and a second compile of the same spec produces a
     byte-identical artefact (determinism).
  3. MCP `/healthz`: spawn `uv run prompiler serve --transport http
     --host 127.0.0.1 --port 0`, parse the bound port from the server log,
     GET `/healthz`, assert `200 {"status":"ok"}`, then terminate the server.
  4. Structured logging: at `--log-level debug`, a compile invocation must
     emit `compile.start`, `compile.done`, and per-stage `stage=` markers.

This script is tolerant of an empty tree: if `src/prompiler/` or `examples/`
do not exist yet, the relevant checks report SKIP with a clear reason rather
than crash. The gate only blocks a release once the surface exists.

Exit codes:
  0  every applicable check passed
  1  at least one applicable check failed
  2  invocation error (uv missing, tree malformed)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
SRC_DIR = REPO_ROOT / "src" / "prompiler"

REQUIRED_SUBCOMMANDS: tuple[str, ...] = ("compile", "extract", "validate", "serve")

# `prompiler serve` is expected to log a line like:
#   "serving on http://127.0.0.1:54321"
# Both http and https are accepted to keep the regex resilient to future
# TLS work.
SERVE_BIND_RE = re.compile(
    r"serving on https?://[^:\s]+:(?P<port>\d+)", re.IGNORECASE
)

HEALTHZ_TIMEOUT_SECONDS = 10.0
HEALTHZ_POLL_INTERVAL = 0.2
SERVER_SHUTDOWN_TIMEOUT = 5.0


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool | None  # None = skipped
    detail: str


def _which_uv() -> str | None:
    return shutil.which("uv")


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _example_specs() -> list[Path]:
    if not EXAMPLES_DIR.is_dir():
        return []
    specs = sorted(
        p for p in EXAMPLES_DIR.glob("*.yaml") if p.is_file()
    )
    specs += sorted(
        p for p in EXAMPLES_DIR.glob("*.yml") if p.is_file()
    )
    return specs


def check_cli_help() -> CheckResult:
    name = "cli --help lists required subcommands"
    if not SRC_DIR.is_dir():
        return CheckResult(
            name,
            None,
            f"skipped: {SRC_DIR.relative_to(REPO_ROOT)} does not exist yet",
        )
    proc = _run(["uv", "run", "prompiler", "--help"], cwd=REPO_ROOT, timeout=60)
    if proc.returncode != 0:
        return CheckResult(
            name,
            False,
            f"`prompiler --help` exited {proc.returncode}: {proc.stderr.strip()}",
        )
    output = proc.stdout + proc.stderr
    missing = [sub for sub in REQUIRED_SUBCOMMANDS if sub not in output]
    if missing:
        return CheckResult(
            name, False, f"missing subcommand(s) in --help: {', '.join(missing)}"
        )
    return CheckResult(name, True, "all required subcommands present")


def check_compile_and_determinism() -> CheckResult:
    name = "compile each example spec (twice → byte-identical)"
    specs = _example_specs()
    if not specs:
        return CheckResult(
            name,
            None,
            "skipped: no example specs found under examples/*.yaml",
        )
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="prompiler-local-test-") as tmp:
        tmp_path = Path(tmp)
        for spec in specs:
            out_a = tmp_path / f"{spec.stem}.a.json"
            out_b = tmp_path / f"{spec.stem}.b.json"
            for out in (out_a, out_b):
                proc = _run(
                    [
                        "uv",
                        "run",
                        "prompiler",
                        "compile",
                        str(spec),
                        "--out",
                        str(out),
                    ],
                    cwd=REPO_ROOT,
                    timeout=120,
                )
                if proc.returncode != 0:
                    failures.append(
                        f"{spec.name}: compile exited {proc.returncode}: "
                        f"{proc.stderr.strip()}"
                    )
                    break
            else:
                a_bytes = out_a.read_bytes()
                b_bytes = out_b.read_bytes()
                if a_bytes != b_bytes:
                    failures.append(
                        f"{spec.name}: artefacts differ "
                        f"({len(a_bytes)}B vs {len(b_bytes)}B)"
                    )
    if failures:
        return CheckResult(name, False, "; ".join(failures))
    return CheckResult(name, True, f"{len(specs)} spec(s) compiled deterministically")


def _wait_for_port_line(
    proc: subprocess.Popen[str], deadline: float
) -> int | None:
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                return None
            time.sleep(HEALTHZ_POLL_INTERVAL)
            continue
        m = SERVE_BIND_RE.search(line)
        if m:
            return int(m.group("port"))
    return None


def _terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=SERVER_SHUTDOWN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=SERVER_SHUTDOWN_TIMEOUT)


def check_mcp_healthz() -> CheckResult:
    name = "MCP /healthz returns 200 {\"status\":\"ok\"}"
    if not SRC_DIR.is_dir():
        return CheckResult(
            name,
            None,
            f"skipped: {SRC_DIR.relative_to(REPO_ROOT)} does not exist yet",
        )
    cmd = [
        "uv",
        "run",
        "prompiler",
        "serve",
        "--transport",
        "http",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
    ]
    env = os.environ.copy()
    # Force unbuffered Python output so the bind-line lands promptly.
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        return CheckResult(name, False, f"failed to spawn server: {exc}")

    try:
        deadline = time.monotonic() + HEALTHZ_TIMEOUT_SECONDS
        port = _wait_for_port_line(proc, deadline)
        if port is None:
            return CheckResult(
                name,
                False,
                "did not observe bind line within "
                f"{HEALTHZ_TIMEOUT_SECONDS:.0f}s",
            )

        # Connect-test the socket first so we don't hammer the URL while the
        # server is still binding.
        url = f"http://127.0.0.1:{port}/healthz"
        last_err: str = ""
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                    break
            except OSError as exc:
                last_err = repr(exc)
                time.sleep(HEALTHZ_POLL_INTERVAL)
        else:
            return CheckResult(
                name, False, f"server bound port {port} but never accepted: {last_err}"
            )

        try:
            with urllib.request.urlopen(url, timeout=5.0) as resp:  # noqa: S310
                status = resp.status
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            return CheckResult(name, False, f"GET {url} failed: {exc}")

        if status != 200:
            return CheckResult(name, False, f"GET {url} returned {status}: {body!r}")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            return CheckResult(
                name, False, f"GET {url} body not JSON ({exc}): {body!r}"
            )
        if parsed != {"status": "ok"}:
            return CheckResult(
                name, False, f"GET {url} body {parsed!r}, expected {{'status':'ok'}}"
            )
        return CheckResult(name, True, f"healthz ok on port {port}")
    finally:
        _terminate(proc)


def check_structured_logging() -> CheckResult:
    name = "structured logging emits compile.start / compile.done / per-stage"
    specs = _example_specs()
    if not specs:
        return CheckResult(
            name,
            None,
            "skipped: no example specs found under examples/*.yaml",
        )
    spec = specs[0]
    with tempfile.TemporaryDirectory(prefix="prompiler-local-test-log-") as tmp:
        out = Path(tmp) / "artefact.json"
        proc = _run(
            [
                "uv",
                "run",
                "prompiler",
                "--log-level",
                "debug",
                "compile",
                str(spec),
                "--out",
                str(out),
            ],
            cwd=REPO_ROOT,
            timeout=120,
        )
    if proc.returncode != 0:
        return CheckResult(
            name,
            False,
            f"debug-level compile exited {proc.returncode}: {proc.stderr.strip()}",
        )
    blob = proc.stdout + proc.stderr
    missing: list[str] = []
    for marker in ("compile.start", "compile.done"):
        if marker not in blob:
            missing.append(marker)
    if "stage=" not in blob:
        missing.append("stage=<name>")
    if missing:
        return CheckResult(
            name,
            False,
            f"missing log marker(s): {', '.join(missing)}",
        )
    return CheckResult(name, True, "all required markers present")


def _report(results: list[CheckResult]) -> int:
    width = max(len(r.name) for r in results)
    sys.stderr.write("\nlocal_test.py — RULES.md §9 results\n")
    sys.stderr.write("=" * (width + 12) + "\n")
    failed = 0
    skipped = 0
    for r in results:
        if r.passed is None:
            status = "SKIP"
            skipped += 1
        elif r.passed:
            status = "PASS"
        else:
            status = "FAIL"
            failed += 1
        sys.stderr.write(f"  [{status}] {r.name.ljust(width)}  {r.detail}\n")
    sys.stderr.write("=" * (width + 12) + "\n")
    sys.stderr.write(
        f"summary: {len(results) - failed - skipped} passed, "
        f"{failed} failed, {skipped} skipped\n"
    )
    return 1 if failed else 0


def main() -> int:
    if _which_uv() is None:
        sys.stderr.write(
            "local_test: `uv` not found on PATH. Install uv (>=0.4.0) and retry.\n"
        )
        return 2
    if not REPO_ROOT.joinpath("pyproject.toml").is_file():
        sys.stderr.write(
            "local_test: pyproject.toml not found at repo root "
            f"({REPO_ROOT}). Aborting.\n"
        )
        return 2

    results: list[CheckResult] = [
        check_cli_help(),
        check_compile_and_determinism(),
        check_mcp_healthz(),
        check_structured_logging(),
    ]
    return _report(results)


if __name__ == "__main__":
    sys.exit(main())
