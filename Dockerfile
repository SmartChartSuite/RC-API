FROM python:3.13-slim

RUN apt-get -y update && \
    apt-get -y install git libpq-dev gcc

COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /uvx /bin/

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml pyproject.toml
COPY uv.lock uv.lock
RUN uv sync --frozen --no-dev

EXPOSE 8080

COPY . .

CMD ["hypercorn", "main:app", "--config", "hypercorn_config.toml"]
