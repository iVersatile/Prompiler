"""FR↔test traceability matrix (P9.2 anti-drift gate).

Source of truth: docs/PLAN.md §P9.2 — "Build an FR↔test traceability matrix;
CI fails when any functional FR maps to zero tests." The matrix below is the
*authoritative* mapping; the assertions fail the build (via the existing
required ``unit`` CI check) if any functional FR loses coverage.

Functional FRs are PRD §6 FR-1..FR-14. FR-15 (Container Distribution) is a
docker-only concern with no pytest surface and is a documented deferral, so it
is intentionally absent from the functional matrix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).parent

# PRD §6 functional FR set (FR-1..FR-14). Titles mirror the PRD headings.
FUNCTIONAL_FRS: dict[str, str] = {
    "FR-1": "Spec Authoring",
    "FR-2": "Compilation",
    "FR-3": "Extraction (callable surface)",
    "FR-4": "Classification",
    "FR-5": "Batch Execution",
    "FR-6": "Eval Harness",
    "FR-7": "Refinement Loop",
    "FR-8": "MCP Server",
    "FR-9": "CLI Surface (v1)",
    "FR-10": "Credential Provider",
    "FR-11": "Configuration Surface",
    "FR-12": "Observability",
    "FR-13": "Doc-Size Handling",
    "FR-14": "Determinism",
}

# Authoritative FR → covering test files. Every functional FR MUST map to at
# least one existing test file that contains at least one ``def test_``.
FR_TEST_MAP: dict[str, tuple[str, ...]] = {
    "FR-1": (
        "test_spec_linter.py",
        "test_spec_loader.py",
        "test_spec_model.py",
        "test_spec_examples.py",
    ),
    "FR-2": (
        "test_compiler_compile.py",
        "test_compiler_model.py",
        "test_compiler_prompt.py",
        "test_compiler_schema.py",
        "test_compiler_walk.py",
        "test_compiler_constraints.py",
        "test_spec_hash.py",
    ),
    "FR-3": (
        "test_runtime_orchestrator.py",
        "test_pipeline.py",
        "test_retry.py",
        "test_runtime_errors.py",
        "test_runtime_failure_modes.py",
    ),
    "FR-4": (
        "test_integration_email_classify.py",
        "test_integration_icd10_classify.py",
    ),
    "FR-5": (
        "test_integration_batch_heterogeneous.py",
        "test_runtime_stress.py",
    ),
    "FR-6": (
        "test_eval_fixtures.py",
        "test_eval_metrics.py",
        "test_eval_report_html.py",
        "test_eval_report_json.py",
        "test_eval_runner.py",
        "test_cli_eval.py",
    ),
    "FR-7": (
        "test_refine_differ.py",
        "test_refine_reeval.py",
        "test_refine_tutor.py",
        "test_cli_refine.py",
        "test_e2e_refine_uplift.py",
    ),
    "FR-8": (
        "test_mcp_server.py",
        "test_mcp_app.py",
        "test_mcp_main.py",
        "test_mcp_runtime.py",
        "test_e2e_mcp.py",
    ),
    "FR-9": (
        "test_cli.py",
        "test_cli_redaction.py",
        "test_e2e_cli.py",
        "test_cli_stats.py",
    ),
    "FR-10": ("test_credentials.py",),
    "FR-11": ("test_pricing_loader.py",),
    "FR-12": (
        "test_observability.py",
        "test_telemetry.py",
        "test_cli_stats.py",
        "test_obs.py",
        "test_usage.py",
    ),
    "FR-13": ("test_runtime_chunk.py",),
    "FR-14": (
        "test_runtime_determinism.py",
        "test_backend_contract.py",
    ),
}


@pytest.mark.unit
def test_every_functional_fr_has_a_mapping() -> None:
    """Each PRD §6 functional FR (FR-1..FR-14) must appear with ≥1 test file."""
    missing = sorted(fr for fr in FUNCTIONAL_FRS if not FR_TEST_MAP.get(fr))
    assert not missing, (
        f"functional FRs with zero mapped tests: {missing} — "
        "every FR-1..FR-14 must map to at least one test file"
    )


@pytest.mark.unit
def test_matrix_has_no_unknown_or_nonfunctional_frs() -> None:
    """Matrix keys must be exactly the functional FR set — no typos, no FR-15."""
    extra = sorted(set(FR_TEST_MAP) - set(FUNCTIONAL_FRS))
    assert not extra, (
        f"FR_TEST_MAP references non-functional/unknown FRs: {extra} — "
        "FR-15 (container) is a documented deferral and must not appear"
    )


@pytest.mark.unit
@pytest.mark.parametrize("fr", sorted(FUNCTIONAL_FRS))
def test_mapped_files_exist_and_contain_tests(fr: str) -> None:
    """Every mapped file must exist and define at least one ``def test_``."""
    for rel in FR_TEST_MAP[fr]:
        path = _TESTS_DIR / rel
        assert path.is_file(), f"{fr}: mapped test file {rel!r} does not exist"
        assert "def test_" in path.read_text(encoding="utf-8"), (
            f"{fr}: mapped file {rel!r} contains no `def test_` function"
        )
