# ---- Builder stage: resolve and install dependencies ----
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- Runtime stage: clean slim image with only the venv + app ----
FROM python:3.14-slim

LABEL org.opencontainers.image.source=https://github.com/SmartChartSuite/RC-API

# Unprivileged runtime user with a fixed UID/GID so volume permissions are predictable.
RUN groupadd --system --gid 10001 rcapi \
    && useradd --system --uid 10001 --gid rcapi --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin rcapi

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1

# Dependencies and application code stay root-owned and read-only to the runtime user.
COPY --from=builder /app/.venv /app/.venv
COPY . .

# Only the /app directory itself is writable, so the default SQLite database
# (sqlite+pysqlite:///rcapi_jobs.sqlite) and its journal can be created there.
RUN chown rcapi:rcapi /app

USER rcapi

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"]

CMD ["hypercorn", "main:app", "--config", "hypercorn_config.toml"]
