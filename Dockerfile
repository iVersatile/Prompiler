# syntax=docker/dockerfile:1.7
#
# Multi-stage production image for prompiler (L59).
#
# Stage 1 (builder): uv-managed virtualenv built from the frozen lock.
# Stage 2 (runtime): distro-slim Python, non-root UID, /healthz HEALTHCHECK,
# OCI labels, multi-arch via `docker buildx`.
#
# Build (single arch, local):
#   docker build -t prompiler:dev .
#
# Build (multi arch, release):
#   docker buildx build \
#     --platform linux/amd64,linux/arm64 \
#     --build-arg SOURCE_URL=https://github.com/<owner>/prompiler \
#     --build-arg VERSION="$(git describe --tags --always)" \
#     --build-arg REVISION="$(git rev-parse HEAD)" \
#     --build-arg LICENSES=Apache-2.0 \
#     -t prompiler:"$(git describe --tags --always)" \
#     --push .
#
# Run (loopback only, default):
#   docker run --rm -p 127.0.0.1:8765:8765 prompiler:dev
#
# Run (bind all interfaces — explicit opt-in, gated by build_server):
#   docker run --rm \
#     -e PROMPILER_MCP_HOST=0.0.0.0 \
#     -e PROMPILER_MCP_ALLOW_NON_LOOPBACK=1 \
#     -p 8765:8765 prompiler:dev
#
# Read-only rootfs (recommended):
#   docker run --rm --read-only --tmpfs /tmp -p 127.0.0.1:8765:8765 prompiler:dev

# ---------- Stage 1: builder ----------
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy lock + manifest first for cache friendliness; src copy invalidates only
# the project-install layer, not the dependency-install layer.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src

# `--no-dev` excludes ruff/mypy/pytest/pre-commit (runtime image stays lean).
# `--no-editable` materializes the project as a wheel into .venv so the runtime
# stage does not need a separate src/ copy or PYTHONPATH.
# `--frozen` refuses to update uv.lock (fail loudly on drift).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

# OCI image labels — populated at build time via `--build-arg`.
ARG SOURCE_URL=""
ARG VERSION="0.0.0"
ARG REVISION=""
ARG LICENSES="Apache-2.0"

LABEL org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="${LICENSES}" \
      org.opencontainers.image.title="prompiler" \
      org.opencontainers.image.description="prompiler MCP server"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROMPILER_MCP_HOST=127.0.0.1 \
    PROMPILER_MCP_PORT=8765

# Non-root user (fixed UID for reproducibility across rebuilds and for
# Kubernetes `runAsUser` policies).
RUN groupadd --system --gid 10001 prompiler \
    && useradd --system --uid 10001 --gid 10001 --home /app --shell /sbin/nologin prompiler

WORKDIR /app

# Pull the prebuilt venv (project + transitive runtime deps) from the builder.
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

USER 10001:10001

EXPOSE 8765

# Use stdlib urllib so the runtime image does not need curl/wget. Loopback only
# inside the container — the actual exposed bind is decided by `-p` at `docker
# run` time.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).status == 200 else 1)"]

# `python -m prompiler.mcp` -> src/prompiler/mcp/__main__.py:main
# (env-driven host/port; loopback-only unless PROMPILER_MCP_ALLOW_NON_LOOPBACK=1).
CMD ["python", "-m", "prompiler.mcp", "--transport", "http"]
