# Lessons Learnt — prompiler

Append-only. Indexed by tag. See `docs/RULES.md` §4 for the workflow.

---

## How to use this file

**Before fixing any bug or regression:**

1. Identify the surface area of the work (e.g., `compiler`, `adapter-claude`, `mcp-server`).
2. For each matching tag, run:
   ```
   grep -E "^- \*\*Tags:\*\* .*\b<tag>\b" docs/LESSONS_LEARNT.md
   ```
3. Read every matching entry in full.
4. Apply any relevant lesson. Cite it in the commit body (e.g., `Applying LL-007`).

**On user "add lesson" command:** append a new entry. Never edit an existing one — if a previous lesson is superseded, write a follow-up entry that references the original by ID and leave the original intact.

---

## Tag registry

Use the smallest set of tags that accurately classify the entry. Add a new tag only when none of the below fits.

| Tag | Surface area |
|-----|--------------|
| `compiler` | Spec → artefact compilation pipeline |
| `runtime` | Backend dispatch, retry, redaction |
| `adapter-claude` | Anthropic Claude adapter |
| `adapter-openai` | OpenAI adapter |
| `adapter-gemini` | Google Gemini adapter |
| `adapter-ollama` | Local Ollama adapter |
| `mcp-server` | MCP server (stdio + HTTP) |
| `cli` | `prompiler` CLI surface |
| `cassettes` | VCR-style fixture recording / playback |
| `container` | Dockerfile, image hardening, supply-chain |
| `ci` | GitHub Actions, matrix, caching, gates |
| `release` | Tagging, changelog, signing, SBOM |

---

## Entry template

Copy this block when appending a new entry. Increment `NNN` from the highest existing ID. Use ISO-8601 (`YYYY-MM-DD`) for the date.

```markdown
## LL-NNN — <short title>

- **Date:** YYYY-MM-DD
- **Tags:** `<surface-1>`, `<surface-2>`
- **Symptom:** What was observed
- **Root cause:** Why it happened
- **Fix:** What was changed
- **Prevention:** How to avoid repeating it
```

---

## Entries

## LL-001 — Adopting pre-commit as a uv dev dep does not install the git hook

- **Date:** 2026-05-22
- **Tags:** `ci`
- **Symptom:** Ruff I001 lint violation in `tests/test_compiler_walk.py` reached `dev` branch and turned CI red. The pre-commit gate defined in `.pre-commit-config.yaml` did not fire locally on this clone.
- **Root cause:** Commit `490a415` ("chore(p0): adopt pre-commit as uv-managed dev dep") added `pre-commit` as a tool dependency but never invoked `uv run pre-commit install`. The git hook scripts under `.git/hooks/` were therefore never written for this clone (only the `.sample` files from `git init` were present). CI invokes `uvx pre-commit run --all-files` directly and bypasses the git plumbing, masking the gap. Two independent enforcement layers were both opt-in and both un-configured on the fresh clone — the project's git hook AND any Claude Code PostToolUse ruff hook.
- **Fix:** Ran `uv run pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg --hook-type prepare-commit-msg` locally. Added a defensive Claude Code PostToolUse hook in the parent `.claude/settings.json` that runs `ruff check --fix` on `*.py` edits as a belt-and-suspenders layer.
- **Prevention:** Treat `CONTRIBUTING.md` setup as load-bearing on every fresh clone. Add a bootstrap step (`make setup` or `scripts/bootstrap.sh`) that runs `uv sync` followed by `uv run pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg --hook-type prepare-commit-msg`, so new clones cannot skip the hook install step. CI should also fail fast if a developer pushed without the hook (e.g. by running `pre-commit run --all-files` in CI, which we already do — keep this gate).
