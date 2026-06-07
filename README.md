# prompiler

Spec-to-artefact prompt compiler with multi-vendor adapters and MCP server.

`prompiler` compiles a declarative specification into deterministic prompt artefacts for Anthropic, OpenAI, Gemini, and Ollama backends, and exposes the compiler over a Model Context Protocol (MCP) server.

## Status

Pre-alpha. Phase P0 (bootstrap). See [`docs/PLAN.md`](docs/PLAN.md) for the roadmap and [`docs/PRD.md`](docs/PRD.md) for the product spec.

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

## License

Apache-2.0. See [`LICENSE`](LICENSE).
