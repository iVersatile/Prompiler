"""prompiler CLI entry point.

P7 migrates the command surface from argparse to typer. Command names, flags,
exit codes, and diagnostic strings are preserved byte-identically so the P0/P1
contract in ``tests/test_cli.py`` holds unchanged.

Wired in pyproject.toml as ``[project.scripts] prompiler = "prompiler.cli:main"``
so ``uv run prompiler ...`` resolves here. Also invoked by the
``prompiler-validate`` pre-commit hook (``.pre-commit-config.yaml`` §2)
which calls ``uv run prompiler validate prompts/`` on every commit.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import click
import typer

from prompiler import __version__
from prompiler.codegen import write as codegen_write
from prompiler.eval import (
    CapturingHook,
    build_report,
    load_fixtures,
    run_eval,
    write_html_report,
    write_report,
)
from prompiler.mcp.server import LOOPBACK_HOST, build_server
from prompiler.obs import configure_logging, get_logger
from prompiler.pricing import load_pricing
from prompiler.refine import apply_patch, propose_patch_sync, run_refine_loop
from prompiler.runtime.errors import AdapterError, EvalError
from prompiler.runtime.registry import Registry, register_from_path
from prompiler.spec.linter import lint_spec
from prompiler.spec.loader import SpecLoadError, load_spec
from prompiler.usage import (
    default_usage_log_path,
    format_summary,
    parse_since,
    read_usage,
    summarize,
)

_log = get_logger(__name__)

_AUTO_APPLY_MAX_ITERATIONS = 3


class _Backend(StrEnum):
    mock = "mock"
    ollama = "ollama"


class _Transport(StrEnum):
    http = "http"


app = typer.Typer(
    name="prompiler",
    help="prompiler — spec-to-artefact prompt compiler.",
    add_completion=False,
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    if value:
        sys.stdout.write(f"prompiler {__version__}\n")
        raise SystemExit(0)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the prompiler version and exit.",
        ),
    ] = False,
) -> None:
    if ctx.invoked_subcommand is None:
        sys.stdout.write(ctx.get_help())
        raise typer.Exit(0)


@app.command()
def validate(
    path: Annotated[
        Path,
        typer.Argument(help="Spec file or directory of prompt specs to validate."),
    ],
) -> None:
    """Validate prompt specs under the given path (load + lint)."""
    raise typer.Exit(_cmd_validate(path))


@app.command()
def codegen(
    spec: Annotated[
        Path,
        typer.Argument(help="Path to the spec YAML file to render."),
    ],
    out_dir: Annotated[
        Path,
        typer.Option(
            "-o",
            "--out-dir",
            help="Output directory for the generated module (default: .prompiler/compiled).",
        ),
    ] = Path(".prompiler/compiled"),
) -> None:
    """Render a spec to a standalone vendored Python module."""
    raise typer.Exit(_cmd_codegen(spec, out_dir))


@app.command()
def serve(
    transport: Annotated[
        _Transport,
        typer.Option(help="Transport for MCP server (only 'http' supported in P0)."),
    ] = _Transport.http,
    host: Annotated[
        str,
        typer.Option(help=f"Bind host (default: {LOOPBACK_HOST})."),
    ] = LOOPBACK_HOST,
    port: Annotated[
        int,
        typer.Option(help="Bind port (default: 8765; 0 selects an ephemeral port)."),
    ] = 8765,
    allow_non_loopback: Annotated[
        bool,
        typer.Option(
            "--allow-non-loopback",
            help="Opt-in to bind a non-loopback host (emits WARN).",
        ),
    ] = False,
) -> None:
    """Run the MCP skeleton HTTP server (P0 healthz only)."""
    raise typer.Exit(_cmd_serve(host, port, allow_non_loopback=allow_non_loopback))


@app.command("eval")
def eval_cmd(
    spec: Annotated[
        Path,
        typer.Argument(help="Path to the spec YAML file to evaluate."),
    ],
    fixtures: Annotated[
        Path,
        typer.Argument(help="Path to the eval fixture YAML file."),
    ],
    backend: Annotated[
        _Backend,
        typer.Option(help="Backend to run the eval against (default: ollama)."),
    ] = _Backend.ollama,
    model: Annotated[
        str | None,
        typer.Option(help="Model name override for the backend."),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option(help="Base URL override for the ollama backend."),
    ] = None,
    json_out: Annotated[
        Path | None,
        typer.Option(help="Write the eval-report.json to this path."),
    ] = None,
    html_out: Annotated[
        Path | None,
        typer.Option(help="Write the eval-report.html dashboard to this path."),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option(help="Per-call timeout in seconds."),
    ] = None,
    expect_hash: Annotated[
        str | None,
        typer.Option(help="Expected spec_hash; a mismatch emits a WARN."),
    ] = None,
    telemetry: Annotated[
        bool,
        typer.Option(
            help="Export OpenTelemetry spans for each backend call (OFF by default).",
        ),
    ] = False,
) -> None:
    """Run a spec against a fixture and emit metrics reports."""
    raise typer.Exit(
        _cmd_eval(
            spec,
            fixtures,
            backend=backend.value,
            model=model,
            base_url=base_url,
            json_out=json_out,
            html_out=html_out,
            timeout=timeout,
            expect_hash=expect_hash,
            telemetry=telemetry,
        )
    )


@app.command()
def refine(
    report: Annotated[
        Path,
        typer.Argument(help="Path to the eval-report.json to refine against."),
    ],
    prompt: Annotated[
        Path,
        typer.Argument(help="Path to the prompt text file to propose a diff over."),
    ],
    backend: Annotated[
        _Backend,
        typer.Option(help="Tutor backend (default: ollama)."),
    ] = _Backend.ollama,
    model: Annotated[
        str | None,
        typer.Option(help="Model name override for the backend."),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option(help="Base URL override for the ollama backend."),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option(help="Per-call timeout in seconds."),
    ] = None,
    auto_apply: Annotated[
        bool,
        typer.Option(
            "--auto-apply",
            help="Run a bounded propose->apply->eval loop instead of printing one diff.",
        ),
    ] = False,
    spec: Annotated[
        Path | None,
        typer.Option(help="Spec YAML to evaluate against (required with --auto-apply)."),
    ] = None,
    fixtures: Annotated[
        Path | None,
        typer.Option(help="Fixtures YAML to evaluate against (required with --auto-apply)."),
    ] = None,
) -> None:
    """Propose a prompt edit from an eval report (tutor diff to stdout)."""
    raise typer.Exit(
        _cmd_refine(
            report,
            prompt,
            backend=backend.value,
            model=model,
            base_url=base_url,
            timeout=timeout,
            auto_apply=auto_apply,
            spec=spec,
            fixtures=fixtures,
        )
    )


@app.command()
def stats(
    since: Annotated[
        str,
        typer.Option(help="Lookback window: e.g. 7d, 24h, 30m, 2w (default: 7d)."),
    ] = "7d",
    log: Annotated[
        Path | None,
        typer.Option(
            help="Usage-log path override (default: $PROMPILER_USAGE_LOG or .prompiler/usage.jsonl).",  # noqa: E501
        ),
    ] = None,
) -> None:
    """Summarise recorded backend usage over a recent time window."""
    raise typer.Exit(_cmd_stats(since=since, log=log))


def _iter_spec_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    files: list[Path] = []
    for pattern in ("*.yaml", "*.yml"):
        files.extend(path.rglob(pattern))
    return sorted(files)


def _cmd_validate(path: Path) -> int:
    if not path.exists():
        sys.stderr.write(f"prompiler validate: path not found: {path}\n")
        return 2

    had_issue = False
    for spec_path in _iter_spec_files(path):
        try:
            spec = load_spec(spec_path)
        except SpecLoadError as exc:
            sys.stderr.write(f"{exc}\n")
            had_issue = True
            continue
        for issue in lint_spec(spec):
            sys.stderr.write(f"{spec_path}:{issue.path} {issue.code}: {issue.message}\n")
            had_issue = True

    return 1 if had_issue else 0


def _cmd_codegen(spec_path: Path, out_dir: Path) -> int:
    if not spec_path.exists():
        sys.stderr.write(f"prompiler codegen: path not found: {spec_path}\n")
        return 2
    if not spec_path.is_file():
        sys.stderr.write(f"prompiler codegen: not a file: {spec_path}\n")
        return 2
    try:
        spec = load_spec(spec_path)
    except SpecLoadError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    out_path = codegen_write(spec, out_dir)
    sys.stdout.write(f"{out_path}\n")
    return 0


def _cmd_serve(host: str, port: int, *, allow_non_loopback: bool) -> int:
    try:
        server = build_server(host=host, port=port, allow_non_loopback=allow_non_loopback)
    except ValueError as exc:
        sys.stderr.write(f"prompiler serve: {exc}\n")
        return 2
    bound_host, bound_port = server.socket.getsockname()[:2]
    sys.stdout.write(f"serving on http://{bound_host}:{bound_port}\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _build_eval_backend(
    backend: str,
    model: str | None,
    base_url: str | None,
    *,
    telemetry: bool = False,
) -> tuple[object, CapturingHook | None, str]:
    if backend == "mock":
        from prompiler.backends.mock import MockAdapter

        return MockAdapter(), None, model or "mock"

    from prompiler.backends.observability import FanOutHook, ObservabilityHook
    from prompiler.backends.ollama import DEFAULT_MODEL, OllamaAdapter
    from prompiler.telemetry import build_telemetry_hook
    from prompiler.usage import FileUsageHook

    hook = CapturingHook()
    hooks: list[ObservabilityHook] = [hook, FileUsageHook(default_usage_log_path())]
    otel = build_telemetry_hook(enabled=telemetry)
    if otel is not None:
        hooks.append(otel)
    fan_out = FanOutHook(hooks)
    kwargs: dict[str, object] = {"observability": fan_out}
    resolved_model = model or DEFAULT_MODEL
    kwargs["model"] = resolved_model
    if base_url is not None:
        kwargs["base_url"] = base_url
    return OllamaAdapter(**kwargs), hook, resolved_model  # type: ignore[arg-type]


def _cmd_eval(
    spec_path: Path,
    fixtures_path: Path,
    *,
    backend: str,
    model: str | None,
    base_url: str | None,
    json_out: Path | None,
    html_out: Path | None,
    timeout: float | None,
    expect_hash: str | None,
    telemetry: bool = False,
) -> int:
    if not spec_path.is_file():
        sys.stderr.write(f"prompiler eval: spec not found: {spec_path}\n")
        return 2
    if not fixtures_path.is_file():
        sys.stderr.write(f"prompiler eval: fixtures not found: {fixtures_path}\n")
        return 2

    try:
        spec = load_spec(spec_path)
    except SpecLoadError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    try:
        cases = load_fixtures(fixtures_path)
    except EvalError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    bundle = register_from_path(spec_path)
    spec_hash = bundle.spec_hash
    if expect_hash is not None and expect_hash != spec_hash:
        _log.warning(
            "spec_hash mismatch: fixture expected %s but current spec is %s",
            expect_hash,
            spec_hash,
        )

    adapter, hook, resolved_model = _build_eval_backend(
        backend, model, base_url, telemetry=telemetry
    )
    try:
        result = run_eval(
            spec.name,
            cases,
            backend=adapter,
            timeout=timeout,
            metrics_hook=hook,
        )
    finally:
        asyncio.run(adapter.aclose())  # type: ignore[attr-defined]

    report = build_report(
        result,
        spec=spec.name,
        spec_hash=spec_hash,
        backend=backend,
        model=resolved_model,
        fixture_path=str(fixtures_path),
    )
    if json_out is not None:
        write_report(report, json_out)
        sys.stdout.write(f"{json_out}\n")
    if html_out is not None:
        write_html_report(report, html_out)
        sys.stdout.write(f"{html_out}\n")

    agg = result.metrics
    sys.stdout.write(
        f"cases={len(result.cases)} "
        f"precision={agg.precision:.3f} recall={agg.recall:.3f} f1={agg.f1:.3f}\n"
    )
    return 0


def _cmd_refine(
    report_path: Path,
    prompt_path: Path,
    *,
    backend: str,
    model: str | None,
    base_url: str | None,
    timeout: float | None,
    auto_apply: bool = False,
    spec: Path | None = None,
    fixtures: Path | None = None,
) -> int:
    if not report_path.is_file():
        sys.stderr.write(f"prompiler refine: report not found: {report_path}\n")
        return 2
    if not prompt_path.is_file():
        sys.stderr.write(f"prompiler refine: prompt not found: {prompt_path}\n")
        return 2

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"prompiler refine: invalid report JSON: {exc}\n")
        return 1

    current_prompt = prompt_path.read_text(encoding="utf-8")
    if auto_apply:
        return _cmd_refine_auto_apply(
            report=report,
            current_prompt=current_prompt,
            backend=backend,
            model=model,
            base_url=base_url,
            timeout=timeout,
            spec=spec,
            fixtures=fixtures,
        )

    adapter, _hook, _resolved_model = _build_eval_backend(backend, model, base_url)
    try:
        diff = propose_patch_sync(
            report=report,
            current_prompt=current_prompt,
            backend=adapter,  # type: ignore[arg-type]
            timeout=timeout,
        )
    except AdapterError as exc:
        sys.stderr.write(f"prompiler refine: {exc}\n")
        return 1
    finally:
        asyncio.run(adapter.aclose())  # type: ignore[attr-defined]

    sys.stdout.write(diff)
    return 0


def _cmd_refine_auto_apply(
    *,
    report: dict[str, Any],
    current_prompt: str,
    backend: str,
    model: str | None,
    base_url: str | None,
    timeout: float | None,
    spec: Path | None,
    fixtures: Path | None,
) -> int:
    if spec is None or fixtures is None:
        sys.stderr.write("prompiler refine: --auto-apply requires --spec and --fixtures\n")
        return 2
    if not spec.is_file():
        sys.stderr.write(f"prompiler refine: spec not found: {spec}\n")
        return 2
    if not fixtures.is_file():
        sys.stderr.write(f"prompiler refine: fixtures not found: {fixtures}\n")
        return 2

    try:
        spec_obj = load_spec(spec)
    except SpecLoadError as exc:
        sys.stderr.write(f"prompiler refine: {exc}\n")
        return 1
    try:
        cases = load_fixtures(fixtures)
    except EvalError as exc:
        sys.stderr.write(f"prompiler refine: {exc}\n")
        return 1

    base_bundle = register_from_path(spec, registry=Registry())
    adapter, _hook, resolved_model = _build_eval_backend(backend, model, base_url)

    def _propose(rep: dict[str, Any], current: str) -> str:
        return propose_patch_sync(
            report=rep,
            current_prompt=current,
            backend=adapter,  # type: ignore[arg-type]
            timeout=timeout,
        )

    def _evaluate(current: str) -> tuple[float, dict[str, Any]]:
        loop_registry = Registry()
        loop_registry.register(spec_obj.name, dataclasses.replace(base_bundle, prompt=current))
        result = run_eval(
            spec_obj.name,
            cases,
            backend=adapter,
            registry=loop_registry,
            timeout=timeout,
        )
        new_report = build_report(
            result,
            spec=spec_obj.name,
            spec_hash=base_bundle.spec_hash,
            backend=backend,
            model=resolved_model,
            fixture_path=str(fixtures),
        )
        return result.metrics.f1, new_report

    try:
        outcome = run_refine_loop(
            initial_prompt=current_prompt,
            initial_report=report,
            propose=_propose,
            apply=apply_patch,
            evaluate=_evaluate,
            max_iterations=_AUTO_APPLY_MAX_ITERATIONS,
        )
    except AdapterError as exc:
        sys.stderr.write(f"prompiler refine: {exc}\n")
        return 1
    finally:
        asyncio.run(adapter.aclose())  # type: ignore[attr-defined]

    for step in outcome.steps:
        sys.stdout.write(f"iteration {step.iteration}: f1={step.f1:.3f}\n")
    sys.stdout.write(outcome.final_prompt)
    return 0


def _cmd_stats(*, since: str, log: Path | None) -> int:
    try:
        window = parse_since(since)
    except ValueError as exc:
        sys.stderr.write(f"prompiler stats: {exc}\n")
        return 1

    for warning in load_pricing().warnings:
        sys.stderr.write(f"prompiler stats: warning: {warning}\n")

    path = log if log is not None else default_usage_log_path()
    records = read_usage(path)
    summary = summarize(records, since=window, now=datetime.now(UTC))
    sys.stdout.write(format_summary(summary) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    command = typer.main.get_command(app)
    try:
        return command(args=argv, standalone_mode=False) or 0
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.exceptions.Abort:
        sys.stderr.write("Aborted!\n")
        return 1
    except click.exceptions.ClickException as exc:
        exc.show()
        return int(exc.exit_code)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
