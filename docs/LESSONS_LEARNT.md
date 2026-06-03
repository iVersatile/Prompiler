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

## LL-002 — Vendor SKU sunset breaks adapter default + cassette + pricing triad

- **Date:** 2026-06-03
- **Tags:** `adapter-gemini`, `cassettes`
- **Symptom:** `nightly-live-smoke.yml` Gemini job returned `404 NOT_FOUND` from `https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent` with body `"models/gemini-1.5-flash is not found for API version v1beta, or is not supported for generateContent"`. Adapter hard-coded the sunset SKU as `DEFAULT_MODEL`; the recorded happy-path cassette URL and the `DEFAULT_PRICING_TABLE` entry both pinned the same SKU.
- **Root cause:** Google retired `gemini-1.5-flash` from the v1beta `generateContent` endpoint, and prompiler had no vendor-deprecation watch. The adapter default SKU, the cassette playback URL, and the pricing table key form a tightly coupled triad — drifting one without the others either breaks live calls (default SKU) or makes the cassette unplayable (URL mismatch) or zeros out cost telemetry (pricing miss). All three referenced the dead SKU.
- **Fix:** Bumped the triad in lockstep to `gemini-2.5-flash` at paid pricing `$0.30 / $2.50` per 1M prompt/completion tokens. Edits: `src/prompiler/backends/gemini.py` `DEFAULT_MODEL`, `src/prompiler/backends/observability.py` `DEFAULT_PRICING_TABLE` entry, `tests/cassettes/gemini_happy_path.json` URL `replace_all`, `tests/test_observability.py` expected-keys + cost assertions. Local pytest: 392 passed; nightly-live-smoke re-dispatched for the `4 passed, 0 skipped` gate required by P2 §7 DoD #3.
- **Prevention:** Treat adapter `DEFAULT_MODEL`, recorded cassette URLs, and `DEFAULT_PRICING_TABLE` keys as a single triad — never bump one in isolation. Add a vendor-model-deprecation watch (calendar items from Anthropic/OpenAI/Google model lifecycle pages) and keep `nightly-live-smoke.yml` as the fast-failing canary so a sunset shows up within 24h instead of at phase-close audit.

## LL-003 — Adapter swallows HTTP error response body on raise_for_status

- **Date:** 2026-06-03
- **Tags:** `adapter-gemini`, `adapter-claude`, `observability`
- **Symptom:** `nightly-live-smoke.yml` Gemini job failed with bare `httpx.HTTPStatusError: Client error '400 Bad Request' for url '...:generateContent'` and no body text. Root-cause diagnosis required either a local API key (unavailable) or a code-patch + re-dispatch round-trip just to surface the vendor's actual `{"error":{"code":400,"message":"..."}}` payload. Symmetric gap exists in the Claude adapter — any 4xx from `/v1/messages` would log the same opaque line.
- **Root cause:** Both `src/prompiler/backends/gemini.py` and `src/prompiler/backends/claude.py` called `response.raise_for_status()` inside their `_do_post` retry closures. `httpx.Response.raise_for_status` raises an `HTTPStatusError` whose message is the generic `f"Client error '{status} {reason}' for url '{url}'"` template — the response body is reachable only via `exc.response.text`, which is not in the exception's `str()`. CI logs and `logging.exception` capture only the message string, so the body is invisible unless the operator manually pulls `exc.response.text` from a debugger. The visibility gap is identical across adapters because the pattern is copy-pasted.
- **Fix:** Replaced `response.raise_for_status()` in `gemini.py` with an explicit `if response.status_code >= 400: raise httpx.HTTPStatusError(f"Gemini {status}: {response.text}", request=..., response=...)` block. The custom message embeds `response.text` so CI logs surface the vendor body on first failure. Retry semantics are preserved because `with_retry._is_transient` keys off `exc.response.status_code`, not the message string. The Claude adapter carries the same gap and migrates in a follow-up commit.
- **Prevention:** Treat `response.raise_for_status()` as a code smell in any adapter that talks to a paid vendor — always embed `response.text` (or a JSON-parsed `error.message` field when the vendor uses a stable error envelope) in the raised exception's message. Add a backend-contract test that asserts any 4xx exception's `str()` contains the response body text, so the gap cannot regress across new adapters. When a `_do_post` closure raises, the message that lands in CI logs IS the diagnostic — make it carry the body.
