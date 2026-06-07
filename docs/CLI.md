# `prompiler`

prompiler — spec-to-artefact prompt compiler.

**Usage**:

```console
$ prompiler [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--version`: Show the prompiler version and exit.
* `--help`: Show this message and exit.

**Commands**:

* `validate`: Validate prompt specs under the given path...
* `codegen`: Render a spec to a standalone vendored...
* `serve`: Run the MCP skeleton HTTP server (P0...
* `eval`: Run a spec against a fixture and emit...
* `refine`: Propose a prompt edit from an eval report...
* `stats`: Summarise recorded backend usage over a...

## `prompiler validate`

Validate prompt specs under the given path (load + lint).

**Usage**:

```console
$ prompiler validate [OPTIONS] PATH
```

**Arguments**:

* `PATH`: Spec file or directory of prompt specs to validate.  [required]

**Options**:

* `--help`: Show this message and exit.

## `prompiler codegen`

Render a spec to a standalone vendored Python module.

**Usage**:

```console
$ prompiler codegen [OPTIONS] SPEC
```

**Arguments**:

* `SPEC`: Path to the spec YAML file to render.  [required]

**Options**:

* `-o, --out-dir PATH`: Output directory for the generated module (default: .prompiler/compiled).  [default: .prompiler/compiled]
* `--help`: Show this message and exit.

## `prompiler serve`

Run the MCP skeleton HTTP server (P0 healthz only).

**Usage**:

```console
$ prompiler serve [OPTIONS]
```

**Options**:

* `--transport [http]`: Transport for MCP server (only &#x27;http&#x27; supported in P0).  [default: http]
* `--host TEXT`: Bind host (default: 127.0.0.1).  [default: 127.0.0.1]
* `--port INTEGER`: Bind port (default: 8765; 0 selects an ephemeral port).  [default: 8765]
* `--allow-non-loopback`: Opt-in to bind a non-loopback host (emits WARN).
* `--help`: Show this message and exit.

## `prompiler eval`

Run a spec against a fixture and emit metrics reports.

**Usage**:

```console
$ prompiler eval [OPTIONS] SPEC FIXTURES
```

**Arguments**:

* `SPEC`: Path to the spec YAML file to evaluate.  [required]
* `FIXTURES`: Path to the eval fixture YAML file.  [required]

**Options**:

* `--backend [mock|ollama]`: Backend to run the eval against (default: ollama).  [default: ollama]
* `--model TEXT`: Model name override for the backend.
* `--base-url TEXT`: Base URL override for the ollama backend.
* `--json-out PATH`: Write the eval-report.json to this path.
* `--html-out PATH`: Write the eval-report.html dashboard to this path.
* `--timeout FLOAT`: Per-call timeout in seconds.
* `--expect-hash TEXT`: Expected spec_hash; a mismatch emits a WARN.
* `--telemetry / --no-telemetry`: Export OpenTelemetry spans for each backend call (OFF by default).  [default: no-telemetry]
* `--help`: Show this message and exit.

## `prompiler refine`

Propose a prompt edit from an eval report (tutor diff to stdout).

**Usage**:

```console
$ prompiler refine [OPTIONS] REPORT PROMPT
```

**Arguments**:

* `REPORT`: Path to the eval-report.json to refine against.  [required]
* `PROMPT`: Path to the prompt text file to propose a diff over.  [required]

**Options**:

* `--backend [mock|ollama]`: Tutor backend (default: ollama).  [default: ollama]
* `--model TEXT`: Model name override for the backend.
* `--base-url TEXT`: Base URL override for the ollama backend.
* `--timeout FLOAT`: Per-call timeout in seconds.
* `--help`: Show this message and exit.

## `prompiler stats`

Summarise recorded backend usage over a recent time window.

**Usage**:

```console
$ prompiler stats [OPTIONS]
```

**Options**:

* `--since TEXT`: Lookback window: e.g. 7d, 24h, 30m, 2w (default: 7d).  [default: 7d]
* `--log PATH`: Usage-log path override (default: $PROMPILER_USAGE_LOG or .prompiler/usage.jsonl).
* `--help`: Show this message and exit.
