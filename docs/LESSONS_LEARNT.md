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

## LL-004 — Schema projection helper exists but live call site bypasses it

- **Date:** 2026-06-03
- **Tags:** `adapter-gemini`, `runtime`
- **Symptom:** After fixing LL-002 (vendor SKU triad) and LL-003 (visible HTTP body), `nightly-live-smoke.yml` Gemini job still returned `400 INVALID_ARGUMENT` from `:generateContent` with body `"Invalid JSON payload received. Unknown name \"additionalProperties\""`. Unit suite was green: `GeminiAdapter.to_tool_schema(PROBE_SCHEMA)` produced a schema with `additionalProperties` correctly stripped (`test_gemini_strips_hostile_keys`). The projection helper worked; the wire payload still carried the hostile key.
- **Root cause:** `GeminiAdapter.extract()` built `tools[0].functionDeclarations[0].parameters` by passing the caller's `json_schema` *directly* — `"parameters": json_schema` — instead of routing it through `self.to_tool_schema(json_schema)`. The projection helper existed and was unit-tested, but it had no live caller. Two parallel pathways into the same field — one going through projection (unit tests) and one bypassing it (the live call site) — meant the unit suite verified a transform the production code never invoked. The cassette didn't catch it either: cassette playback in `_cassette_transport.py` matches method + URL only, never compares request bodies, so the recorded `additionalProperties`-bearing request replayed identically without protest.
- **Fix:** Changed `gemini.py:136` from `"parameters": json_schema,` to `"parameters": self.to_tool_schema(json_schema),` so the live path goes through the same projection as the unit tests. Added `tests/test_backend_to_tool_schema.py::test_gemini_extract_strips_hostile_keys_on_wire` which uses `httpx.MockTransport` to capture the outgoing request body and assert no `GEMINI_DROP_KEYS` member appears anywhere in `body["tools"][0]["functionDeclarations"][0]["parameters"]`. The regression test fails against the pre-fix code (raw `json_schema` reaches the wire) and passes against the fix.
- **Prevention:** When a helper transforms a payload before it leaves the process, the test that proves the helper works is *not* the same as the test that proves the helper is *called* on the wire. For every schema-projection / redaction / normalisation helper, add a transport-level test that intercepts the outgoing request (`httpx.MockTransport` for httpx-based adapters) and asserts the wire body satisfies the invariant the helper promises. Cassette playback is unsafe as a regression guard for request-shape bugs because it does not verify the request body — only the URL. Make "single source of truth for the wire payload" a design rule per adapter: there must be exactly one site where the structured-output schema enters the request payload, and that site must call the projection helper.

## LL-005 — Truthy env-var escape hatch bypasses the gate it was meant to gate

- **Date:** 2026-06-04
- **Tags:** `ci`
- **Symptom:** `scripts/check_branch_guard.py` (the pre-commit gate that refuses commits on `main`/`master` per RULES.md §1.0) intended to require an explicit `ALLOW_MAIN_COMMIT=1` opt-in but used `if os.environ.get(_ESCAPE_HATCH_ENV, "").strip():` as the check. Any non-empty value — including `0`, `false`, `no`, the literal string `"unset"` — bypassed the guard. PR-review surface only; no production bypass occurred.
- **Root cause:** Conflated "operator deliberately opted in" with "env var has any string value." Python's `bool("0")` and `bool("false")` are both `True`, so a script that *reads* a boolean intent from a string env var must compare against the canonical opt-in token explicitly. The RULES.md §1.0 contract said "set `ALLOW_MAIN_COMMIT=1`," but the code accepted any truthy string and silently permitted a class of accidental bypasses (e.g. a leftover `ALLOW_MAIN_COMMIT=0` from a previous shell export).
- **Fix:** Tightened the check to `if os.environ.get(_ESCAPE_HATCH_ENV, "").strip() == "1":` so only the documented opt-in value disables the guard. Matches the RULES.md §1.0 wording verbatim and removes the foot-gun.
- **Prevention:** Treat any env-var-driven safety gate as a *string equality* problem, not a truthiness problem. The opt-in value belongs in a named constant next to the env-var name (`_ESCAPE_HATCH_ENV = "ALLOW_MAIN_COMMIT"` + `_ESCAPE_HATCH_VALUE = "1"`) so the contract is grep-able. When reviewing a new gate, search the script for `if os.environ.get(...)` patterns and reject any that don't compare against a literal — boolean coercion of env-var strings is a smell.

## LL-006 — Broad Exception type in injected-fault adapter hides fault-class drift

- **Date:** 2026-06-04
- **Tags:** `runtime`
- **Symptom:** `tests/test_runtime_failure_modes.py::_ScriptedAdapter` typed its scripted fault queue as `list[dict[str, Any] | Exception]` and used `isinstance(head, Exception)` to decide whether to raise vs. return. Any future test author could enqueue an arbitrary exception (`ValueError`, `RuntimeError`, `KeyError`) and the adapter would dutifully raise it — even though the orchestrator's documented fault surface is the narrow union `AdapterError | asyncio.TimeoutError`. Tests would pass for fault classes that the orchestrator is not contracted to handle.
- **Root cause:** Using `Exception` as a type bound in a fault-injection harness conflates "this is a Python exception" with "this is a fault the system under test claims to handle." The test fixture's type signature should *mirror the contract under test*, not its widest plausible superset. The broader type accidentally invited drift: a test could quietly start asserting on a fault class the orchestrator never promised to surface, and nothing would flag it.
- **Fix:** Narrowed both the `__init__` parameter and the stored attribute to `list[dict[str, Any] | AdapterError | asyncio.TimeoutError]`, and tightened the `isinstance` check to the same union. Now adding a new fault class to the test suite forces a deliberate edit at both sites — the type system surfaces the contract-expansion as a diff reviewers must sign off on.
- **Prevention:** When writing fault-injection harnesses, derive the injected-fault type from the system-under-test's documented fault surface, not from `Exception`. Treat the harness signature as a second source of truth for the contract: if it widens, the contract widens with it, and vice versa. For adapter-style protocols this typically means `BaseAdapterError | asyncio.TimeoutError` or the equivalent narrow union — never bare `Exception` or `BaseException`.

## LL-007 — Phase checkbox ticked without code passed the phase gate

- **Date:** 2026-06-04
- **Tags:** `ci`, `release`
- **Symptom:** While closing P3 (Runtime), the P2 checklist in `docs/PLAN.md` carried `- [x] Per-call observability hook (latency, tokens, cost estimate via pricing table).` The box was ticked, yet grepping `src/prompiler/{compiler,backends,runtime}/` and `cli.py` found zero lifecycle/per-call emission sites — `obs.py` ships logging infrastructure (`JSONLinesFormatter`, `configure_logging`, `redact_payload`) but nothing in the pipeline emits `codegen.start`/`stage=<name>` markers. A stale `.scratch/p2-resume/STATE.md` had the same item as `[ ]` "in progress" on 2026-05-22; it got promoted to `[x]` without the implementation ever landing. P2 was tagged `v0.2.0-p2` with a false-complete line item.
- **Root cause:** The §7 Phase Done Gate verifies that every checkbox is *checked* and that exit criteria have quoted evidence, but it never verified that a checked implementation line maps to actual code. A checkbox is operator-asserted state; nothing cross-checked the assertion against the source tree. Compounding it, the observability work was always P7 scope (PLAN.md §P7 owns structured logs + cost estimate via `pricing/v1.json` + `prompiler stats`) — the P2 line was misplaced P7 scope that should never have been a P2 deliverable. So the gate passed a box that was both unimplemented *and* mis-located.
- **Fix:** De-scoped (Path B), doc-only — no code written, because the deliverable genuinely belongs to P7 and building it in P2 would double-build. Unchecked `docs/PLAN.md` L132 back to `[ ]` with an explicit "Deferred to P7" note; updated `docs/RULES.md` §9 #5 SKIP rationale from "P2-deferred" (implying imminent P2 landing) to "P7-deferred" (never landed in P2, belongs to P7). No retroactive change to the `v0.2.0-p2` tag — the history stands; the correction lives forward.
- **Prevention:** The §7 Phase Done Gate must verify code presence for any checklist item that claims an implementation, not just the checkbox state — grep the source tree for the named artifact (a function, an emission site, a module) before accepting the box as done. A checkbox is a claim; the gate must confirm the claim against the tree. Secondary: when a line item's natural home is a later phase, do not list it as a current-phase deliverable "to be safe" — misplaced scope is how a never-built item acquires a tick. Cross-check `.scratch/*/STATE.md` against `PLAN.md` before trusting either as "what's done."

## LL-008 — "Determinism" claimed for compile but never plumbed through runtime extraction

- **Date:** 2026-06-09
- **Tags:** `runtime`
- **Symptom:** The project advertised "deterministic" output and the determinism check (`MANUAL_TESTING.md` §7) asserted byte-identical *compile* artefacts, but runtime `extract` was not deterministic end-to-end. `temperature` and `seed` were accepted as `extract()` kwargs yet never resolved from any configuration source, and for backends that honour `seed` (Ollama, OpenAI) the value did not reliably reach the request payload. Backends that ignore `seed` (Claude, Gemini) silently dropped it with no signal to the caller. A user reading "deterministic" reasonably expected repeatable extraction; two identical `extract` calls could diverge with nothing flagging why.
- **Root cause:** "Determinism" was treated as a single-surface property proven at compile time, when it is actually a *two-surface contract* — compile-time artefact stability AND runtime sampling control. The kwargs existed on the Protocol signature (`temperature=0.0`, `seed=42`) which created the appearance of a wired feature, but there was no precedence chain feeding them from real configuration and no test asserting the value reached the wire. The signature was a promise the call path never kept — the same shape of bug as LL-004 (helper exists, unit-tested, but the live path bypasses it), here applied to sampling parameters instead of a schema projection. Non-seed backends compounded it: dropping an unsupported parameter without warning is a silent failure that hides the divergence's cause.
- **Fix:** Added an orchestrator precedence chain — kwarg → `PROMPILER_TEMPERATURE`/`PROMPILER_SEED` env → `pyproject.toml [tool.prompiler]` → hardcoded default (temp `0.0`, seed `42`) via a `_UNSET` sentinel so an explicit `None`/`0` is distinguishable from "not supplied." Wired `seed` into the request payload for the seed-honouring adapters (Ollama, OpenAI) and added FR-14 warn-once: one `seed unsupported` WARN per non-seed backend per process, keyed by `type(backend).__name__` in `orchestrator._warned_backends`. Tagged the trace record (`obs.TRACE`, `event="deterministic"`) with `deterministic` and `system_fingerprint`. Added `tests/test_runtime_determinism.py` (15 tests) asserting seed reaches the wire for Ollama/OpenAI, the warn fires once for Claude/Gemini, and the precedence chain resolves correctly. Tightened `MANUAL_TESTING.md` §7 so the runtime recipe asserts byte-identical Ollama output (`diff -q` exits 0) rather than outputs that merely "should match."
- **Prevention:** Treat "deterministic" as a contract that must be proven on *every* surface that produces output — compile AND runtime — not just the one that's easiest to make byte-stable. When a Protocol signature exposes a knob (here `temperature`/`seed`), there must be (a) a test that the resolved value reaches the wire for backends that honour it, and (b) an explicit, observable signal (WARN, not silent drop) for backends that don't. Mirror LL-004's wire-level test discipline for sampling parameters: `httpx.MockTransport` capturing the outgoing body is the source of truth, not the cassette (which never compares request bodies). A signature kwarg with no precedence chain behind it is a false promise — wire the chain or remove the kwarg.
