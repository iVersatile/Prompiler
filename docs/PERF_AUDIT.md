# v2 Performance-Budget Verification (Q5 / I2)

Confirms the PRD §7.1 budgets still hold after the v2 additions (result cache,
streaming, spec composition). The two network-free budgets stay gated in CI; the
three backend/process-dominated budgets are the §7.1 manual-testing carve-out and
are recorded here from a local measurement run.

**Measured at:** `feat/q5-hardening-docs-release` @ `09b200e`, Python 3.11.15,
arm64 / Darwin. Backend for `run_batch`: local Ollama `llama3.2:1b`.

**Method:** best-of-N min timing (one warm-up call, then the minimum of 20 runs)
for the hot paths; a single real timed run for `run_batch` and a launch→`/healthz`
wall-clock window for MCP cold start. Min-of-N is the least-noisy estimator on a
shared host — scheduler jitter only ever inflates a sample.

## Results

| Budget (PRD §7.1) | Limit | Measured | Verdict |
|-------------------|-------|----------|---------|
| compile (single spec) | < 200 ms | 1.4 ms | met |
| validate (single spec) | < 50 ms | 1.5 ms | met |
| run overhead (single call, network excluded) | < 50 ms | 0.6 ms | met |
| `run_batch` 100 @ conc=8 (Ollama local) | < 60 s | 8.0 s | met |
| MCP server cold start | < 1 s | 319 ms | met |

`compile` and `validate` are the CI-gated pair — asserted by
`tests/test_perf_budgets.py` (`uv run pytest -m perf -q` → 20 passed), parametrized
over every spec in `examples/`; the table reports the worst spec.

## Carve-out notes

The three budgets below are excluded from the required CI checks because their
timings are dominated by a live backend or a spawned process and would be flaky as
gates. They are verified here instead (PRD §7.1 manual-testing carve-out).

| Budget | Note |
|--------|------|
| **run overhead** | Measured with an in-memory, schema-valid adapter so the number is pure orchestration cost (cache-key, prompt build, Pydantic validate) with the network leg excluded. Overhead is spec-independent; 3 of the 9 example specs validate against a trivially-generated payload (the other 6 carry per-field regex/enum constraints the trivial adapter can't satisfy) and the worst of those is reported. 0.6 ms is two orders of magnitude under the 50 ms budget. |
| **`run_batch`** | One real run of 100 items at concurrency 8 against local Ollama `llama3.2:1b`, all 100 returning a valid extraction (100/100). 8.0 s leaves a ~7× margin under the 60 s budget; the figure scales with the chosen model and is not a code-path regression signal. |
| **MCP cold start** | Wall-clock from `python -m prompiler.mcp --transport http` launch to the first `200` from `/healthz` (interpreter boot + package import + server ready). 319 ms is well under the 1 s budget. |

## Conclusion

All five PRD §7.1 budgets are met with comfortable margins; no regression was
introduced by the v2 additions. **No fix was required, so no `Applying LL-NNN`
cite is carried** (the cite attaches to fixes; none were made).
