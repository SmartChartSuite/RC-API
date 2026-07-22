# ---- Builder stage: resolve and install dependencies ----
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /uvx /bin/

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- Runtime stage: clean slim image with only the venv + app ----
FROM python:3.13-slim

LABEL org.opencontainers.image.source=https://github.com/SmartChartSuite/RC-API

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

COPY --from=builder /app/.venv /app/.venv

EXPOSE 8080

COPY . .

CMD ["hypercorn", "main:app", "--config", "hypercorn_config.toml"]
