# CLAUDE.md — prompiler

Agent-facing entry point. Thin pointer; **does not** restate rules.

## Source of truth

All project-wide rules live in [`docs/RULES.md`](docs/RULES.md). Read that file before taking any action that touches code, commits, tags, or phase boundaries. If this file and `docs/RULES.md` ever disagree, `docs/RULES.md` wins — fix the divergence here.

## Inheritance

Project rules sit on top of the user's global rules under `~/.claude/rules/common/` and `~/.claude/rules/python/`. Where conflicts exist, project rules win. Where `docs/RULES.md` is silent, global rules apply.

## Quick map for agents

| If the user says... | Go read... |
|---------------------|------------|
| `go`, `go:verify` | `docs/RULES.md` §1 |
| "commit", "push", "ship" | `docs/RULES.md` §2 + §3 (gates) |
| "add lesson", "what does LL-NNN say" | `docs/RULES.md` §4 + `docs/LESSONS_LEARNT.md` |
| "tag", "release", "bump version" | `docs/RULES.md` §5 |
| "start phase N", "next phase" | `docs/RULES.md` §6 + `docs/PLAN.md` |
| "phase done", "close phase" | `docs/RULES.md` §7 + `docs/PLAN.md` |
| "local test", "verify locally" | `docs/RULES.md` §9 + `docs/MANUAL_TESTING.md` |
| "what's the spec", "what are we building" | `docs/PRD.md` |
| "how is it structured" | `docs/architecture.md` |

## Hard constraints (inherited from `docs/RULES.md` §8)

- No hardcoded credentials. Env vars or secret manager only.
- No `--dangerously-skip-permissions`, `--no-verify`, `sudo`.
- No GPU-only deps. CPU-only hosts must work.
- No prompt or response payloads logged except at `PROMPILER_LOG_LEVEL=trace`.
- No credentials in stdout/stderr.
- Cassette wire bodies must be redacted before commit.

The pre-commit and pre-push gates enforce most of the above. Do not rely on agent discipline.

## When in doubt

Ask the user. Do not self-approve phase completion, tag creation, or any irreversible push under any circumstance.
