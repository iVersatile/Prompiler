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
        help="Validate prompt specs under the given path (P0 stub: existence check only).",
    )
    validate.add_argument(
        "path",
        type=Path,
        help="Directory of prompt specs to validate.",
    )
    return parser


def _cmd_validate(path: Path) -> int:
    if not path.exists():
        sys.stderr.write(f"prompiler validate: path not found: {path}\n")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args.path)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
