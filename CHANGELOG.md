# Changelog

All notable changes to prompiler are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

At release time the `release.yml` workflow auto-generates a raw commit-level
`RELEASE_NOTES.md` for the GitHub release; this file is the curated, human-readable
counterpart that groups changes by capability.

## [0.2.0] - 2026-06-14

Second feature release ("v2"), batching four workstreams: credential/auth handling,
spec composition, end-to-end streaming, and release hardening.

### Breaking

- **Spec `spec_version` is now `2`** — a clean break from v1. `load_spec` rejects
  `spec_version: 1` documents and points authors at the new `prompiler migrate-spec`
  command. `spec_hash` now digests the *flattened* `extends` form, so v2 hashes are
  not comparable to v1 hashes.

### Added

- **Credentials & auth.** `KeychainProvider` reads backend credentials from the OS
  keychain; `OAuthProvider` plus a new `prompiler login` command handle OAuth token
  acquisition and refresh. Backend selection now resolves through a documented
  precedence chain: explicit kwarg → environment variable → `pyproject.toml`.
- **`refine --auto-apply`.** The refine loop can apply proposed spec edits
  automatically, guarded by a fail-closed git working-tree check.
- **Spec composition (`extends`).** Specs may declare an `extends` parent reference;
  `load_spec` flattens inherited fields before validation. Added `prompiler
  migrate-spec` to upgrade v1 specs to v2.
- **Streaming.** `BackendAdapter` gained a streaming contract. Claude, OpenAI, and
  Gemini stream extractions over SSE; Ollama streams over NDJSON. A `run_stream`
  orchestrator entrypoint drives streaming runs, is result-cache aware, and is
  threaded through the MCP `extract` tool. Streaming can be opted out of, with an
  automatic non-streaming fallback.
- **Self-contained packaging.** The example specs ship as package data so wheel and
  container installs resolve them via `importlib.resources`.

### Changed

- Version bumped `0.1.3` → `0.2.0` (`pyproject.toml` and `__version__` in lockstep).
- Documentation refreshed to the v2 surface: README, tutorial, CLI/API reference,
  architecture, and PRD scope.

### Fixed

- Validate OAuth `expires_in` before computing token expiry.
- `refine --auto-apply` fails closed when the git tree state is unreadable.
- `extends` inherited-field validation errors resolve to the parent file and line.
- Migrated specs are written atomically to avoid truncation on a mid-write crash.
- Reject `extends` references with leading or trailing whitespace.
- Vendor-prefix streaming parse failures so streamed and buffered results stay at
  parity.
- Package example specs so wheel/container installs resolve them (previously the MCP
  server failed to start under a non-editable install).

### Security

- Recorded the `extends` path trust model in ADR 0002 (no path confinement; trust
  boundary documented).
- Completed a v2 security/secret audit confirming no payload or credential leakage in
  the v2 surface.

[0.2.0]: https://github.com/iVersatile/Prompiler/compare/v0.1.3...v0.2.0
