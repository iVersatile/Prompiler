"""prompiler CLI entry point.

P0 stub. Real subcommands land in P1+ once the EntitySpec parser exists.

Wired in pyproject.toml as ``[project.scripts] prompiler = "prompiler.cli:main"``
so ``uv run prompiler ...`` resolves here. Also invoked by the
``prompiler-validate`` pre-commit hook (``.pre-commit-config.yaml`` §2)
which calls ``uv run prompiler validate prompts/`` on every commit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from prompiler import __version__
from prompiler.codegen import write as codegen_write
from prompiler.mcp.server import LOOPBACK_HOST, build_server
from prompiler.obs import configure_logging
from prompiler.spec.linter import lint_spec
from prompiler.spec.loader import SpecLoadError, load_spec


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prompiler",
        description="prompiler — spec-to-artefact prompt compiler.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"prompiler {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    validate = subparsers.add_parser(
        "validate",
        help="Validate prompt specs under the given path (load + lint).",
    )
    validate.add_argument(
        "path",
        type=Path,
        help="Spec file or directory of prompt specs to validate.",
    )

    codegen = subparsers.add_parser(
        "codegen",
        help="Render a spec to a standalone vendored Python module.",
    )
    codegen.add_argument(
        "spec",
        type=Path,
        help="Path to the spec YAML file to render.",
    )
    codegen.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path(".prompiler/compiled"),
        help="Output directory for the generated module (default: .prompiler/compiled).",
    )

    serve = subparsers.add_parser(
        "serve",
        help="Run the MCP skeleton HTTP server (P0 healthz only).",
    )
    serve.add_argument(
        "--transport",
        choices=["http"],
        default="http",
        help="Transport for MCP server (only 'http' supported in P0).",
    )
    serve.add_argument(
        "--host",
        default=LOOPBACK_HOST,
        help=f"Bind host (default: {LOOPBACK_HOST}).",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bind port (default: 8765; 0 selects an ephemeral port).",
    )
    serve.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Opt-in to bind a non-loopback host (emits WARN).",
    )
    return parser


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


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args.path)
    if args.command == "codegen":
        return _cmd_codegen(args.spec, args.out_dir)
    if args.command == "serve":
        return _cmd_serve(args.host, args.port, allow_non_loopback=args.allow_non_loopback)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
