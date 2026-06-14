# v2 Security & Secret Audit (Q5 / I1)

Hardening audit for the v2 surface. Two questions: do the secret/SAST scans
come back clean, and does any new v2 code path leak a prompt/response payload
(above `trace`) or a credential to stdout/stderr (RULES §8)?

**Scope:** the v2 additions over v1 — multimodal modal bytes, streaming chunks,
keychain/OAuth credential read+refresh, and the `refine --auto-apply` spec write.

**Audited at:** `feat/q5-hardening-docs-release` @ `93efe26`.

## Scans

| Scan | Command | Result |
|------|---------|--------|
| Secret scan (Layer 1, repo-tuned) | `python scripts/scan_secrets.py --files $(git ls-files)` | `rc=0` — clean |
| Secret scan (Layer 2, provider rule pack) | `uv run pre-commit run gitleaks --all-files` | Passed |
| SAST | `uvx bandit -r src/` | 12 Low / 2 Medium / 1 High — **0 true positives** (triage below) |

### bandit triage

Every finding is a false positive or an accepted-in-context invariant check.
None is a real vulnerability.

| ID | Sev | Location | Verdict |
|----|-----|----------|---------|
| B701 | High | `codegen.py` `autoescape=False` | False positive — Jinja env emits a **Python module**, not HTML; output is never browser-served. HTML escaping would corrupt generated code. |
| B506 | Med ×2 | `eval/fixtures.py:104`, `spec/loader.py:138` | False positive — both use `Loader=_LineMappingLoader`, a subclass of `yaml.SafeLoader`. No arbitrary-object construction. |
| B603/B607 | Low | `cli.py` git subprocess calls | Safe — fixed `argv` (`git rev-parse`/`git status`), `shell=False`, `check=False`. No user-interpolated shell string. |
| B105 | Low ×4 | `cli.py` env-var name constants | False positive — these are env-var **names** (e.g. the OAuth env-var keys), not secret values. |
| B101 | Low ×3 | `compiler/constraints.py`, `compiler/walk.py` | Accepted — `assert` guards internal invariants on already-validated AST/spec nodes; the untrusted-input path raises `ValueError`, not `assert`. |

## §8 leakage audit — v2 paths

| Path | Finding |
|------|---------|
| **Observability** (`backends/observability.py`) | `BackendCallMetrics` / `emit_call_metrics` carry only `backend`, `model`, `latency_seconds`, `prompt_tokens`, `completion_tokens`, `cost_usd`. Payload-free by construction — no prompt text, response body, or modal bytes in the metrics path. |
| **Redaction** (`obs.py`) | `redact_payload(value, *, level)` returns the value only when `level <= TRACE`, else `"REDACTED"`. Payloads are gated below `trace` exactly as RULES §8 requires. |
| **Orchestrator** (`runtime/orchestrator.py`) | `_warn_if_nondeterministic` logs only the backend **class name** (FR-14); `_trace_deterministic` logs only `deterministic` / `system_fingerprint` flags, and only at `obs.TRACE`. Nothing above `trace` carries a payload. |
| **MCP server** (`mcp/server.py`) | Logs only host / port / client address / event metadata. No payloads. |
| **Credentials** (`backends/credentials.py`) | Zero `logging` / `print` / stdout / stderr calls — the keychain read and OAuth refresh legs cannot leak a token or refresh secret. |
| **`prompiler login`** (`cli.py`) | Reads OAuth env vars but echoes only the **token-store path** on success (`primed {backend} credentials at {path}`); the token value is never written to stdout/stderr. Error writes carry paths + exception messages only. |
| **Modal bytes** (claude / openai / gemini / ollama adapters) | `part.data` is only ever `base64.b64encode`'d into the HTTP request payload (vendor dialect). Modal bytes never reach a logger, `print`, or stdout/stderr. |
| **Streaming chunks** (`backends/_pipeline.py`) | `iter_sse_data` / `iter_ndjson_data` have no logging; they `yield json.loads(...)` only. Response text reaches `truncate_for_error` (200-char clamp) **only** inside a 4xx/5xx `HTTPStatusError` message — a vendor error body, not a streamed payload. |
| **`refine --auto-apply`** (`cli.py` `_cmd_refine_auto_apply`) | `_apply` writes only the **refined prompt text** to `prompt_path` (the artifact being optimized, not a secret). stdout = iteration f1 / stop-reason / final prompt; stderr = paths + exception messages. No credential path. The git-tree guard fails closed on `dirty`/`unknown` before any write. |

## Conclusion

Both secret scans are clean; bandit has zero true positives; no v2 code path
leaks a prompt/response payload above `trace` or a credential to stdout/stderr.
**No fix was required, so no `Applying LL-NNN` cite is carried** (the cite
attaches to fixes; none were made).
