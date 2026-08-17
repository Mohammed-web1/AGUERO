# syntax=docker/dockerfile:1

FROM python:3.11-slim AS base

# Install uv (fast Python package/dependency manager) via the official static binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Recommended env vars for uv in containers
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (better layer caching): only pyproject.toml + uv.lock
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Now copy the rest of the project source
COPY . .

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Put the venv on PATH so `python`/entrypoints work without `uv run`
ENV PATH="/app/.venv/bin:$PATH"

CMD ["echo", "Python Client Placeholder"]
