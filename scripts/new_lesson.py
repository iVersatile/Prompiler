#!/usr/bin/env python3
"""Scaffold a new LESSONS_LEARNT entry with the next available ID.

Source of truth: docs/RULES.md §4.

Reads ``docs/LESSONS_LEARNT.md``, finds the highest existing ``LL-NNN`` ID, and
emits a template entry stamped with today's ISO-8601 date and the requested
title/tags. The placeholder ``_None yet._`` is replaced on first append.

Usage:

    python scripts/new_lesson.py --title "<short title>" --tags <tag1>,<tag2>
        [--append]

Without ``--append`` the scaffold is printed to stdout for review. With
``--append`` the scaffold is written to ``docs/LESSONS_LEARNT.md`` in place.

Exit codes:
    0  scaffold emitted (or appended) successfully
    1  invocation error (missing title, unreadable file)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LESSONS_FILE = REPO_ROOT / "docs" / "LESSONS_LEARNT.md"

ID_PATTERN = re.compile(r"^## LL-(\d{3})\b", re.MULTILINE)
PLACEHOLDER = "_None yet._"


def _next_id(content: str) -> str:
    ids = [int(m.group(1)) for m in ID_PATTERN.finditer(content)]
    nxt = max(ids, default=0) + 1
    return f"LL-{nxt:03d}"


def _scaffold(lesson_id: str, title: str, tags: list[str]) -> str:
    today = _dt.date.today().isoformat()
    tag_str = ", ".join(f"`{t.strip()}`" for t in tags if t.strip()) or "`<tag>`"
    return (
        f"## {lesson_id} — {title}\n"
        "\n"
        f"- **Date:** {today}\n"
        f"- **Tags:** {tag_str}\n"
        "- **Symptom:** <what was observed>\n"
        "- **Root cause:** <why it happened>\n"
        "- **Fix:** <what was changed>\n"
        "- **Prevention:** <how to avoid repeating it>\n"
    )


def _append(content: str, entry: str) -> str:
    if PLACEHOLDER in content:
        return content.replace(PLACEHOLDER, entry.rstrip() + "\n")
    if not content.endswith("\n"):
        content += "\n"
    return content + "\n" + entry


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Scaffold a new LESSONS_LEARNT entry."
    )
    parser.add_argument("--title", required=True, help="Short title for the lesson.")
    parser.add_argument(
        "--tags",
        default="",
        help="Comma-separated surface-area tags (e.g., compiler,cli).",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append the scaffold to docs/LESSONS_LEARNT.md in place.",
    )
    args = parser.parse_args(argv[1:])

    try:
        content = LESSONS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"new_lesson: cannot read {LESSONS_FILE}: {exc}", file=sys.stderr)
        return 1

    lesson_id = _next_id(content)
    tags = [t for t in args.tags.split(",") if t.strip()]
    entry = _scaffold(lesson_id, args.title, tags)

    if args.append:
        updated = _append(content, entry)
        LESSONS_FILE.write_text(updated, encoding="utf-8")
        print(f"new_lesson: appended {lesson_id} to {LESSONS_FILE}")
    else:
        sys.stdout.write(entry)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
