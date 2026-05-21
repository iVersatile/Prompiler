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

## Development

Project rules live in [`docs/RULES.md`](docs/RULES.md). The pre-commit gate (ruff, mypy, pytest -m unit with coverage) runs on `git commit`. See [`docs/MANUAL_TESTING.md`](docs/MANUAL_TESTING.md) for the test tiers and [`CLAUDE.md`](CLAUDE.md) for the agent-facing entry point.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
