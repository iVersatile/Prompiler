# prompiler — Project Rules

Canonical project-wide rules for `prompiler`. Apply to every contributor and every agent invocation.

This document is the source of truth. `CLAUDE.md` at the repo root is a thin pointer for AI agents and must not diverge from this file.

---

## 1. `go` Command

Single phase-aware command. Each invocation runs one task end-to-end: implement, gate, pause for review, commit, push, advance the plan.

### 1.1 Steps

1. Read `docs/PLAN.md`. Identify the current in-progress task — the first unchecked checkbox in the active phase.
2. Cross-check `docs/LESSONS_LEARNT.md` (see §4) for any tag that matches the task surface area. Apply applicable lessons before writing code.
3. Implement the task.
4. Run the local pre-commit gate (§2). If red, stop and report — do not commit.
5. Pause for user review of the diff. Wait for explicit confirmation ("ship", "go", "lgtm") before step 6.
6. Run the pre-push gate (§3).
7. Commit with a conventional-commits message. Cite any applied `LL-NNN` in the commit body.
8. Push. Do **not** poll remote CI in the foreground — the user verifies CI green via `gh run watch` or notification.
9. Mark the implemented task checkbox complete in `docs/PLAN.md`.
10. Mark the next task checkbox as in-progress in `docs/PLAN.md`.
11. Report to the user: commit SHA, branch, next task.

### 1.2 Phase-aware gating

If the current task is the **first task of a new phase**, the §6 Phase Start Gate runs before step 3. If the current task is the **last task of the active phase**, the §7 Phase Done Gate runs after step 8 and before step 9 — phase completion requires explicit user approval.

### 1.3 `go:verify` (optional, status-only)

1. Query `gh run list --branch <current> --limit 1 --json status,conclusion`.
2. Report green/red. If red, surface failing job logs.

One-shot status check. Not a wait-loop. Does not modify state.

---

## 2. Pre-Commit Gate

Wired via `.pre-commit-config.yaml`. Runs automatically on `git commit`. Do **not** rely on agent discipline to invoke these manually.

In order:

```
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/prompiler
pytest tests/ -m unit --cov=prompiler --cov-fail-under=80
```

All four must pass. Pytest is scoped to the unit tier (per `docs/MANUAL_TESTING.md` §2) to keep the gate under ~30 s wall time. Integration and e2e tiers run in CI, not in the pre-commit hook.

### Staging Rule

Before `git add`, the agent must run `git diff --name-only` and list every modified path. Stage every directory that contains changes. A scoped `git add <path>` is acceptable only when the agent has confirmed no other paths were modified in this work unit. A partial stage is the most common cause of "passes locally, fails CI".

---

## 3. Pre-Push Gate

Wired via `.git/hooks/pre-push` or `pre-commit`'s `pre-push` stage. Runs automatically on `git push`.

Checks:

1. No uncommitted changes in the working tree (`git status --porcelain` must be empty).
2. No leaked secrets in any commit being pushed. Runs `python scripts/scan_secrets.py --diff <range>` over the commit range.

The pre-commit gate already proved tests green at commit time; the pre-push gate does **not** re-run pytest. This avoids duplicating §2.

---

## 4. `docs/LESSONS_LEARNT.md`

Append-only file. Format below. Each entry is tagged so future agents can index by surface area without reading the entire file.

```
## LL-NNN — <short title>

- **Date:** YYYY-MM-DD
- **Tags:** `<surface-1>`, `<surface-2>`
- **Symptom:** What was observed
- **Root cause:** Why it happened
- **Fix:** What was changed
- **Prevention:** How to avoid repeating it
```

### Tagging convention

Tags are kebab-case surface-area labels. Examples: `compiler`, `runtime`, `adapter-claude`, `adapter-openai`, `adapter-gemini`, `adapter-ollama`, `mcp-server`, `cli`, `cassettes`, `container`, `ci`, `release`. Add a new tag only when no existing tag fits.

### Agent workflow

Before fixing any bug or regression:

1. `grep -E "^- \*\*Tags:\*\* .*\b<tag>\b" docs/LESSONS_LEARNT.md` for each tag matching the current surface area.
2. Read every matching entry in full.
3. Cite the applied lesson in the commit body using the exact phrase `Applying LL-NNN` (e.g., `Applying LL-007`).

### Hook-enforced citation (commit-msg stage)

`scripts/check_lesson_cite.py` runs at the `commit-msg` stage of pre-commit. It is wired in `.pre-commit-config.yaml` and blocks any commit whose subject matches `^(fix|perf)(\([^)]+\))?!?:` unless the message body contains either:

- A citation matching `\bApplying LL-\d{3}\b`, **or**
- A trailer line matching `^Lesson-skip: <reason>$` where `<reason>` is at least 10 characters.

The `Lesson-skip:` trailer is the escape hatch for `fix:` / `perf:` commits where no transferable lesson exists (e.g., a typo, a one-off vendor quirk with no generalisable prevention). The reason field is mandatory and is read by reviewers — "n/a", "none", and other low-signal strings will fail the ≥10-char check. Subjects outside the `fix:` / `perf:` set (`feat`, `docs`, `test`, `chore`, `refactor`, `ci`, `release`) pass through without citation.

This rule is enforced by the hook, not by agent discipline. Do not rely on remembering it — the commit will fail at `git commit` time if the body is missing.

### Docs-only relaxation (prepare-commit-msg stage)

`scripts/prepare_commit_msg.py` runs at the `prepare-commit-msg` stage. When `git diff --cached --name-only` returns only `*.md` paths, it appends a hint block to the commit-message buffer pointing the author at a bare `Lesson-skip:` trailer. For docs-only diffs, `scripts/check_lesson_cite.py` accepts the trailer at minimum length 0 — i.e., a bare `Lesson-skip:` with no reason text is sufficient. The relaxation is bounded to diffs whose every changed path ends `.md`; the moment a non-Markdown file enters the stage, the regular ≥10-char rule re-applies. This removes friction from doc-only `fix:` / `perf:` commits (typo fixes, link repairs, copy-edits) without weakening the gate for any commit that touches code.

### Adding a new lesson

On user "add lesson" command:

1. Run `python scripts/new_lesson.py --title "<short title>" --tags "<tag1>,<tag2>" --append` to scaffold the next `LL-NNN` block at the end of `docs/LESSONS_LEARNT.md` with today's date pre-filled. Omit `--append` to preview the scaffold on stdout instead.
2. Fill in the `Symptom`, `Root cause`, `Fix`, and `Prevention` fields.
3. Commit the new lesson **before** the fix commit that cites it, so the `Applying LL-NNN` reference points to an entry that already exists in the working tree.

Never edit existing entries — append a follow-up entry that supersedes the old one if needed, and leave the original intact for historical context.

---

## 5. Version Tag Gate

Before proposing or applying a new git tag:

1. Verify remote CI is green on `main` (`gh run list --branch main --limit 1`).
2. Run the local test gate (§9).
3. Generate a changelog of commits since the last tag (`git log <last-tag>..HEAD --oneline`).
4. Suggest the next semver bump (patch/minor/major) with rationale tied to the changelog.
5. **Wait for explicit user approval** before creating the tag. Do not self-approve.

---

## 6. Phase Start Gate

Before beginning any new phase in `docs/PLAN.md`:

1. Verify remote CI is green on `main`.
2. Run a scoped code review across files changed since the previous phase tag — **not** a full-repo review. Concretely:
   ```
   git diff <previous-phase-tag>...HEAD --name-only
   ```
   Feed this list to the `code-reviewer` agent with the brief: quality, security, performance, and any tech debt that should be paid down before adding new surface area.
3. Present findings and improvement options to the user.
4. **Wait for explicit user approval** before starting the phase.

The scoped review keeps token cost bounded as the project grows.

### 6.1 First-phase carve-out

The Phase Start Gate does **not** apply to the very first task of the very first phase (P0). At that moment:

- There is no prior CI history to verify green.
- There is no `<previous-phase-tag>` to diff against for the scoped review.

The gate resumes from the first task of the **second** phase onward, using the tag of the just-completed phase as the diff base. A synthetic baseline tag (e.g., `v0.0.0-bootstrap`) cut on `main` after the initial bootstrap commit gives §6 step 2 a concrete reference point for P1's first invocation.

---

## 7. Phase Done Gate

Before marking a phase complete in `docs/PLAN.md`:

1. Every task checkbox in the phase is checked.
2. Every exit criterion documented in `docs/PLAN.md` for that phase is met. Quote the criterion and the evidence inline in the report.
3. Remote CI is green on `main`.
4. **Ask the user for explicit approval.** Do not self-approve phase completion under any circumstance.

---

## 8. General Constraints (inherited)

- No hardcoded API keys, tokens, or credentials in any file. Always use environment variables or a secret manager. Validate required env vars at startup with a clear error if missing.
- No `--dangerously-skip-permissions`, `--no-verify`, or `sudo`.
- No GPU-only dependencies. The compiler and adapters must run on CPU-only hosts.
- No prompt or response payloads logged at any level except when `PROMPILER_LOG_LEVEL=trace` is set explicitly by the operator. Default and DEBUG levels log metadata only (spec hash, latency, token counts).
- No credentials echoed, logged, or printed to stdout/stderr.
- All adapter wire bodies in cassettes must be redacted of auth headers before commit. The `pre-commit` cassette-redaction hook runs first, but cross-check on review.

A regex-based secret scanner enforces the first and last bullets at commit and push time — see `scripts/scan_secrets.py` and `.pre-commit-config.yaml`.

---

## 9. Local Test Gate

Wrapper script: `scripts/local_test.py`.

**When to run:**

- Before creating or proposing a version tag (in addition to §5).
- After any change to `src/prompiler/compiler/` or `src/prompiler/adapters/`.
- After adding or modifying any example spec under `examples/`.

**What it checks** (pytest deliberately excluded — covered by §2):

1. `uv run prompiler --help` exits 0 and lists `compile`, `extract`, `validate`, `serve`.
2. `uv run prompiler compile <each example spec>` exits 0 and emits a deterministic artefact.
3. Determinism: each example spec is compiled twice; the two artefacts must be byte-identical.
4. MCP healthz: `uv run prompiler serve --transport http --host 127.0.0.1 --port 0` runs in the background; the script discovers the bound port from the log line, curls `/healthz`, asserts `200 {"status":"ok"}`, then terminates the server.
5. Structured logging: at `--log-level debug`, a compile invocation must emit per-stage decision records and `compile.start` / `compile.done` markers. The script asserts these via stdout/stderr capture.

The local test gate verifies expected behaviour against committed example specs before a release. It catches regressions that pass unit tests but break end-to-end CLI or MCP behaviour.

---

## 10. `spec_hash` and `COMPILER_PROTOCOL_VERSION`

The compiler keys every cached artefact by a content-addressed `spec_hash`:

```
spec_hash = SHA-256( canonical_yaml(spec) || COMPILER_PROTOCOL_VERSION )
```

`COMPILER_PROTOCOL_VERSION` is a module-level constant in `src/prompiler/__init__.py` (string, starts at `"1"`). It is **not** the project's release version. Patch and minor releases of `prompiler` must not invalidate downstream caches — only changes that alter the compiler's wire-level output should.

### When to bump

Bump `COMPILER_PROTOCOL_VERSION` (and only then) if any of the following change in a way that produces different bytes from the same input spec:

1. The internal AST grammar — node kinds, field names, evaluation order.
2. The per-adapter projection schema — JSON Schema emitted for Claude, OpenAI, Gemini, Ollama tool/function-call payloads.
3. The canonical-YAML serialisation rules — key ordering, scalar style, anchor handling, the rules the hasher feeds before SHA-256.

### When not to bump

Do not bump for: bug fixes that produce identical output for valid inputs, CLI/UX changes, MCP transport changes, internal refactors, dependency updates, documentation, or test changes. The constant tracks *output identity*, not project identity.

### Rationale

Decoupling the cache key from `prompiler.__version__` means downstream projects can upgrade `prompiler` on patch/minor bumps without rebuilding every artefact under `.prompiler/compiled/`. Generated codegen files (P1) embed both `COMPILER_PROTOCOL_VERSION` and `spec_hash` as module-level constants so drift between a vendored compiled file and the live spec is detectable by a single import + equality check.

---

## Inheritance from `~/.claude/rules/`

These project rules sit on top of the user's global rules in `~/.claude/rules/common/` and `~/.claude/rules/python/`. Where conflicts exist, project rules win. Where this file is silent, global rules apply.
