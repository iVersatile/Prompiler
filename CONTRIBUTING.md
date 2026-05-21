# Contributing to prompiler

Thanks for considering a contribution. This file is a thin pointer; the rules
themselves live elsewhere so they have one source of truth.

## Before you start

1. Read [`docs/RULES.md`](docs/RULES.md). It defines the pre-commit gate, the
   pre-push gate, the version-tag gate, and the phase-start / phase-done gates.
   Everything that can block a merge is described there.
2. Read [`docs/PLAN.md`](docs/PLAN.md) to understand which phase the project is
   in. Work that lands outside the current phase will be rejected.
3. Read [`docs/PRD.md`](docs/PRD.md) to confirm the change you want to make
   fits the product's stated scope.

If you are an automated agent (Claude Code, Aider, Cursor, etc.) read
[`CLAUDE.md`](CLAUDE.md) first — it is the agent-facing entry point and routes
you to the same rules.

## Development setup

```bash
uv sync
uv run pytest -q
uv run pre-commit install
```

Python `>=3.11,<3.12` is required. Dependencies are managed with `uv`; the
lockfile (`uv.lock`) is committed and authoritative.

## Branch model

- `main` is release-only. It is protected — see [`docs/RULES.md`](docs/RULES.md)
  §11. You cannot push to it directly.
- `dev` is the daily-progress branch. All topic branches are cut from `dev` and
  merged back into `dev` via PR.
- Release tags (`vMAJOR.MINOR.PATCH`) are cut on `main` after a `dev → main`
  merge. See [`docs/RULES.md`](docs/RULES.md) §5.

Topic branch naming: `<type>/<short-slug>` where `<type>` is one of `feat`,
`fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

## Commits

Conventional Commits format:

```
<type>: <description>

<optional body>
```

`fix:` and `perf:` commits that change runtime behaviour must cite a lesson
from [`docs/LESSONS_LEARNT.md`](docs/LESSONS_LEARNT.md) in a `Lesson:` trailer.
The commit-msg hook enforces this. Docs-only `fix:`/`perf:` commits may use a
bare `Lesson-skip:` trailer — `scripts/prepare_commit_msg.py` will suggest one
automatically when the staged diff is `*.md`-only.

## Pull requests

A PR may merge into `dev` only if:

- All four required status checks are green: `unit`, `integration`, `e2e`,
  `pre-commit`.
- The branch is up to date with the latest `dev`.
- At least one reviewer has approved. Approvals are dismissed on every new push
  (see [`docs/RULES.md`](docs/RULES.md) §11).
- Linear history is preserved — squash-merge or rebase-merge only.

## Reporting bugs

Open an issue at <https://github.com/iVersatile/Prompiler/issues>. Include:

- `prompiler --version`
- Python version and OS
- Minimal reproducer (a small `.yaml` spec is ideal)
- Expected vs actual output

## Reporting security issues

Do **not** open a public issue. Use GitHub's private security advisory:
<https://github.com/iVersatile/Prompiler/security/advisories/new>.

## Code of Conduct

By participating, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Contributions are licensed under the Apache License 2.0 — see [`LICENSE`](LICENSE)
and [`NOTICE`](NOTICE). By submitting a PR you assert that you have the right
to license the contribution under those terms.
