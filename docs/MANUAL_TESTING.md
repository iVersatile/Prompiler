# Manual Testing — prompiler

This document is the step-by-step recipe for exercising every tier of the
test pipeline on a local developer machine. It mirrors the tiered CI strategy
described in `PLAN.md` §5 and `architecture.md` §3.

Use this doc for:

- Local development sanity checks before pushing.
- Reproducing CI failures on a developer machine.
- Verifying determinism, performance budgets, and cassette playback.
- Driving the optional live-smoke tier when chasing real-API drift.

---

## 0. Prerequisites

- **Python**: 3.11.x (CPython). Verify with `python3 --version`.
- **uv**: 0.4.0 or newer. Install via the [official instructions](https://docs.astral.sh/uv/). Verify with `uv --version`.
- **Docker**: Docker Desktop 4.30+ or Docker Engine 26+ with the `compose` plugin and `buildx`. Verify with `docker version` and `docker compose version`.
- **Disk**: ~2 GB free (Ollama image ~1.2 GB + `qwen2.5:0.5b` model ~0.4 GB + headroom).
- **Optional vendor credentials** (only needed for live-smoke):
  - `ANTHROPIC_API_KEY`
  - `OPENAI_API_KEY`
  - `GOOGLE_APPLICATION_CREDENTIALS` (path to ADC JSON) or `gcloud auth application-default login`

No vendor credentials are required for the unit, integration, or e2e tiers — they
run fully offline against Ollama and recorded cassettes.

---

## 1. Bootstrap

Clone and sync dependencies once.

```bash
git clone https://github.com/<org>/prompiler.git
cd prompiler
uv sync --frozen
```

Quick sanity check — the CLI tree should render:

```bash
uv run prompiler --help
```

---

## 2. Unit Tier

Pure functions, compiler, schema synthesis. No network, no Docker.

- Budget: < 30 s wall time.
- Backends: mocks only.

```bash
uv run pytest -m unit -q
```

Expected: all tests pass, total wall time well under 30 s.

---

## 3. Integration Tier

Adapter contracts and the runtime layer against a live Ollama sidecar plus
recorded cassettes for paid backends.

- Budget: < 2 min wall time.
- Backends: Ollama (local sidecar) + cassette playback (Claude, OpenAI, Gemini).

### 3.1 Start the Ollama sidecar

```bash
docker compose -f docker-compose.test.yml up -d
docker compose -f docker-compose.test.yml logs -f ollama-init   # wait for "digest verified"
```

The compose file pins both the Ollama image and the `qwen2.5:0.5b` model
manifest by SHA256 digest. The first run downloads ~0.4 GB into the named
volume `prompiler-ollama-models`; subsequent runs reuse the cache and the
`ollama-init` one-shot exits immediately after re-verifying the digest.

### 3.2 Run the integration tier

```bash
uv run pytest -m integration -q
```

### 3.3 Tear down

```bash
docker compose -f docker-compose.test.yml down
```

If the Ollama container fails to start, check `docker compose logs ollama` —
the most common cause is insufficient disk space for the pinned model digest.

---

## 4. E2E Tier

CLI subprocess exercises and the MCP server end-to-end (stdio + HTTP).

- Budget: < 5 min wall time.
- Backends: Ollama (still up from §3) + cassettes.

```bash
docker compose -f docker-compose.test.yml up -d
uv run pytest -m e2e -q
```

Each e2e test spawns a real `prompiler` subprocess and asserts CLI output
and MCP tool-call round-trips. If a test hangs, kill it with `Ctrl-C` and
inspect the spawned process tree with `ps -ef | grep prompiler`.

The same tier pins golden artefacts (compiled prompt, JSON schema, `spec_hash`)
for the scenario clients in `tests/test_e2e_clients.py` — the v2-change drift
detector. See [V2 change validation](V2_VALIDATION.md) for how to read and
regenerate those snapshots.

---

## 5. Cassette Record / Playback

Default mode is **strict playback** — cassettes are read-only and any
unrecorded request fails the test loudly.

### 5.1 Playback (default)

No configuration needed. Tests deterministically replay
`tests/cassettes/*.yaml`.

### 5.2 Re-recording (opt-in, requires real keys)

```bash
export PROMPILER_CASSETTE_MODE=record
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/adc.json

uv run pytest -m integration -q --record
```

This rewrites every cassette touched by the run. **Review the diff carefully**
before committing — cassettes capture wire-level requests and responses and
must be free of credentials. The pre-commit hook redacts known auth headers,
but cross-check before pushing.

---

## 6. Optional Live-Smoke Tier

Real-API drift detection against vendor production endpoints. Gated behind an
explicit env var so it never runs by accident.

- Budget: < 10 min wall time.
- Backends: real Claude, OpenAI, Gemini.
- Cost: a handful of cents per run; aborts early on budget breach.

```bash
export PROMPILER_LIVE=1
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
# Gemini uses ADC by default; ensure `gcloud auth application-default login` is done.

uv run pytest -m live -q
```

Without `PROMPILER_LIVE=1`, every live-tier test is skipped. CI runs this
tier nightly only.

---

## 7. Determinism Check

The compiler must produce byte-identical artefacts for the same spec across
runs. Verify locally:

```bash
uv run prompiler compile examples/invoice.yaml -o /tmp/run1.json
uv run prompiler compile examples/invoice.yaml -o /tmp/run2.json
diff -q /tmp/run1.json /tmp/run2.json   # must exit 0
```

For runtime determinism (against Ollama):

```bash
docker compose -f docker-compose.test.yml up -d
uv run prompiler extract examples/invoice.yaml --input examples/invoice_sample.txt -o /tmp/extract1.json
uv run prompiler extract examples/invoice.yaml --input examples/invoice_sample.txt -o /tmp/extract2.json
diff -q /tmp/extract1.json /tmp/extract2.json   # must exit 0
```

With `temperature=0` and `seed=42` (the defaults), the two Ollama outputs must
be byte-identical — `diff -q` exits 0. Ollama honours `seed`, so the request
carries it in the payload; the orchestrator resolves the value via the
kwarg → `PROMPILER_SEED` env → `[tool.prompiler]` → hardcoded-`42` precedence.
Any divergence is a determinism regression — capture both files and open an
issue. For backends that ignore `seed` (Claude, Gemini), expect one
`seed unsupported` WARN per process and verify only `temperature=0` stability.

---

## 8. Performance Budget Spot Check

The NFR budgets from `PRD.md` §7.1 are asserted in CI, but you can sample
them locally:

```bash
uv run pytest -m perf -q
```

Per-spec budgets (typical machine):

| Operation | Budget |
|-----------|--------|
| `prompiler compile <spec>` cold | < 500 ms |
| `prompiler compile <spec>` warm | < 100 ms |
| `prompiler validate prompts/` (50 specs) | < 2 s |
| MCP `/healthz` round-trip | < 20 ms |

If a budget is breached locally but passes in CI, suspect host noise (other
processes, thermal throttling) before filing a regression.

---

## 9. Container Image Smoke Test

Verify the production image builds, runs as non-root, and serves `/healthz`.

### 9.1 Build locally (current arch only)

```bash
docker build -t prompiler:dev .
```

### 9.2 Confirm non-root and read-only rootfs

```bash
docker run --rm prompiler:dev id          # expect uid=10001(prompiler)
docker run --rm --read-only --tmpfs /tmp prompiler:dev prompiler --version
```

### 9.3 Serve and hit `/healthz`

```bash
docker run -d --rm --name prompiler-smoke \
  -p 127.0.0.1:8765:8765 \
  prompiler:dev prompiler serve --transport http --host 0.0.0.0
# wait a moment for startup
curl -fsS http://127.0.0.1:8765/healthz
docker stop prompiler-smoke
```

`--host 0.0.0.0` is required **inside the container** so the bind is reachable
from the host. The startup log will include a WARN line confirming the
non-loopback bind — this is intentional and documented behaviour.

### 9.4 Multi-arch build (optional)

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t prompiler:dev-multi .
```

### 9.5 Verify supply-chain artefacts on a released image

```bash
IMAGE=ghcr.io/<org>/prompiler:<tag>
cosign verify "$IMAGE" \
  --certificate-identity-regexp '.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
cosign verify-attestation --type cyclonedx "$IMAGE" \
  --certificate-identity-regexp '.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ollama` container exits immediately | Disk pressure or model digest mismatch | `docker compose -f docker-compose.test.yml pull ollama`, free disk space |
| Cassette mismatch error | Adapter wire format changed | Re-record cassettes (§5.2); review diff carefully |
| `prompiler: command not found` | Forgot `uv run` prefix or `uv sync` | `uv sync && uv run prompiler --help` |
| `/healthz` returns 503 | Spec registry still loading | Wait a few seconds; check logs for spec parse errors |
| Live tier requests skipped | `PROMPILER_LIVE` unset | `export PROMPILER_LIVE=1` and ensure all vendor keys are present |
| Determinism diff non-empty | Backend ignored `seed`, or spec hash changed mid-run | Re-run with `PROMPILER_LOG_LEVEL=debug` and inspect request payloads |

---

## 11. Credential Leak Sanity Check

Quick scan to catch credential material that may have slipped into git history,
shell history, or recorded cassettes. Run periodically and before any release.

```bash
# Real-looking provider keys anywhere in git history (pickaxe).
git log -p --all -S 'sk-ant-api03-' | head    # Anthropic
git log -p --all -S 'AIzaSy'        | head    # Google
git log -p --all -S 'sk-proj-'      | head    # OpenAI project

# Shell history (zsh).
grep -E 'sk-ant-api03-|AIzaSy|sk-proj-' ~/.zsh_history

# Cassettes (HTTP recordings) — common leak path on first record run.
grep -rE 'sk-ant-api03-|AIzaSy|sk-proj-' tests/cassettes/ 2>/dev/null
```

All four commands should return empty. A hit on any of them means real key
material has been persisted — rotate that provider's key immediately, then
audit the leak path (git history rewrite, history line removal, cassette
re-redaction).

Test fixtures using fake placeholders such as `"sk-ant-1"` are benign — real
keys are much longer (Anthropic ~108 chars, prefix `sk-ant-api03-`). The
prefixes above are tuned to avoid those false positives.

---

## 12. Reporting Local Test Failures

When opening an issue from a local run, include:

1. `uv run prompiler --version` output.
2. `docker compose -f docker-compose.test.yml ps` output.
3. The exact pytest command and the failing test ID.
4. The last ~50 lines of relevant container logs.
5. OS, arch, and Python version.
