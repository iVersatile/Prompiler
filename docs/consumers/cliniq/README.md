# cliniq consumer PoC — DRAFT

> **Status: DRAFT / reference only.** Nothing here is wired into prompiler's
> runtime or import path, and none of it is pytest-collected (`testpaths =
> ["tests"]` excludes `docs/`). These are worked examples showing how cliniq
> would adopt prompiler as the implementation of its planned-but-unbuilt
> *Bridgeform* library (cliniq Phase 2B: P2B-05 ExtractorRegistry, P2B-06
> PromptCompiler + SchemaBuilder). Names, prompts, and golden values are
> illustrative and must be validated against cliniq's real fixtures before any
> code lands in that repo.

## Why this exists

cliniq specs an internal lib (*Bridgeform*) that "compiles a structured entity
specification into an LLM system prompt, a Pydantic model, and a registered
extractor callable." That is functionally prompiler. Rather than build
Bridgeform from scratch, cliniq can register `EntitySpec` YAMLs with prompiler
and call `run_sync`. This PoC takes the two thinnest real entities — **medication**
and **appointment** — through that path end to end.

## Artifact map

| File | Role |
|------|------|
| `medication.spec.yaml` | EntitySpec for medication; top-level `medications` array-of-object |
| `appointment.spec.yaml` | EntitySpec for appointment; top-level `appointments` array-of-object |
| `prompiler_adapter.py` | Thin shim: wraps cliniq `LLMAdapter` to satisfy prompiler `BackendAdapter` |
| `extract_medications_prompiler.py` | Rewrite of cliniq `extract_medications` over `run_sync` |
| `extract_appointments_prompiler.py` | Rewrite of cliniq `extract_appointments` over `run_sync` |
| `test_golden_prompiler.py` | Golden mapping-parity tests (Layer 1, see below) |

## Shim vs. real backend — the adoption decision

cliniq's `LLMAdapter` is **sync** (`complete`, `complete_json(system, user, schema)`).
prompiler's `BackendAdapter` is **async** (`extract(*, prompt, json_schema, …)
-> ExtractResult`, plus `supports` and `to_tool_schema`). Two ways to bridge:

**Option A — native prompiler backend (end-state).** Point cliniq at a real
prompiler backend (Claude/OpenAI/Gemini/Ollama). Full feature surface: seeds
where honoured, multimodal, streaming, proper `to_tool_schema` dialect
projection. Bigger blast radius (new dependency on a network backend + key
management). For cliniq's offline-first stance, the natural choice is a **local
Ollama** backend so "no internet required" survives.

**Option B — thin shim (this PoC).** `prompiler_adapter.PrompilerBackendAdapter`
wraps cliniq's *existing* `LLMAdapter`. Minimal blast radius — cliniq keeps its
adapter and just feeds it into `run_sync`. Cost: degraded fidelity —
  * sync→async via `asyncio.to_thread`;
  * prompiler's single assembled prompt collapses onto the adapter's `system`
    slot with `user=""` (cliniq's two-slot contract is not used);
  * `deterministic=False` always (no honoured seed);
  * `supports(...)=False` always (no seed/multimodal/streaming guarantees);
  * `to_tool_schema` is identity (deep copy).

**Recommendation:** use the shim (Option B) to de-risk the mapping cheaply, then
migrate to Option A (local Ollama) as the end-state once parity is established.

## Two-layer validation framing

Golden tests here prove **Layer 1 — mapping parity**: the seam moves from
`adapter.complete_json` to `BackendAdapter.extract`, and the list payload
round-trips through `run_sync` → `model_dump()[field]` → cliniq model unchanged.
The mock returns a fixed `ExtractResult`, so no model is called.

They do **not** prove **Layer 2 — prompt/output parity**: whether prompiler's
*compiled* prompt elicits the same extraction quality as cliniq's hand-tuned
`_SYSTEM` prompts from a real LLM. That is the real prompt-drift risk and must
be measured separately against cliniq's accuracy fixtures — out of scope for
this harness.

## List-shaped extraction

`run_sync` returns ONE `BaseModel`. To extract a *list*, each spec's top-level
field is an array-of-object (`medications` / `appointments`). The rewritten
extractor reads `extracted.model_dump()["medications"]` and re-validates each
item through cliniq's own model, preserving the original per-item
`ValidationError`-skip so one malformed row never drops the batch.
