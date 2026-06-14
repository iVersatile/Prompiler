# prompiler

Spec-to-artefact prompt compiler with multi-vendor adapters and MCP server.

`prompiler` compiles a declarative specification into deterministic prompt artefacts for Anthropic, OpenAI, Gemini, and Ollama backends, and exposes the compiler over a Model Context Protocol (MCP) server.

## Features

- **Declarative specs with composition.** Author extraction specs in YAML (`spec_version: 2`) and share common fields across specs with `extends`.
- **Deterministic codegen.** Compile a spec into a byte-stable prompt, a Pydantic v2 model, and per-backend tool schemas.
- **Multi-vendor adapters.** Anthropic, OpenAI, Gemini, and Ollama behind one `BackendAdapter` contract.
- **Multimodal input.** Attach images and other modal parts (`ModalContent` / `modal_parts`) alongside the document text.
- **Streaming.** Consume incremental results via `run_stream` (`StreamEvent`) from backends that support it.
- **Caching.** A spec→artefact compile cache and a call→result cache, both on by default; opt out with `PROMPILER_DISABLE_CACHE` or `disable_cache=True`.
- **Self-correcting extraction.** `run` / `run_sync` validate the response against the model and perform one corrective retry on failure.
- **Iterative refinement.** `prompiler refine --auto-apply` runs a bounded propose→apply→evaluate loop against fixtures.
- **Credential management.** `prompiler login` stores OAuth tokens; keychain and OAuth providers resolve vendor credentials.
- **Spec migration.** `prompiler migrate-spec` rewrites a v1 spec to v2 in place (idempotent).
- **MCP server.** Expose the compiler over the Model Context Protocol for agent tooling.

## Status

The v2 feature set is complete; the project is in its final hardening, docs, and release phase. See [`docs/PLAN.md`](docs/PLAN.md) for the roadmap and [`docs/PRD.md`](docs/PRD.md) for the product spec.

New here? Start with the [Tutorial](docs/TUTORIAL.md) for a cold-install-to-extraction walkthrough, then the [Python API reference](docs/API.md).

## Requirements

- Python `>=3.11,<3.12`
- [uv](https://docs.astral.sh/uv/) for environment and dependency management

## Quickstart

```bash
uv sync
uv run prompiler --help
```

## Pre-commit hook (downstream projects)

If your project keeps prompt specs under version control, validate them on every
commit with [pre-commit](https://pre-commit.com/). `prompiler validate` loads and
lints each spec, exiting `0` when all specs are valid, `1` on a lint error, and
`2` when the path does not exist.

Add a local hook to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: prompiler-validate
        name: validate prompt specs
        entry: prompiler validate
        language: system
        files: ^specs/.*\.ya?ml$
        pass_filenames: false
        args: ["specs/"]
```

`prompiler` must be importable in the hook environment — either install it into
the same virtualenv pre-commit runs in, or pin it via a managed environment
(`language: python` with `additional_dependencies: ["prompiler"]`).

## Development

Project rules live in [`docs/RULES.md`](docs/RULES.md). The pre-commit gate (ruff, mypy, pytest -m unit with coverage) runs on `git commit`. See [`docs/MANUAL_TESTING.md`](docs/MANUAL_TESTING.md) for the test tiers and [`CLAUDE.md`](CLAUDE.md) for the agent-facing entry point.

The CLI reference at [`docs/CLI.md`](docs/CLI.md) is generated from the Typer app — do not edit it by hand. Regenerate it after changing any command surface:

```bash
uv run typer src/prompiler/cli.py utils docs --name prompiler --output docs/CLI.md
```

## License

Apache-2.0. See [`LICENSE`](LICENSE).
