# Stage 1: Build virtual environment with uv
FROM python:3.14-slim AS builder

# Install uv binary from official Astral image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy dependency specifications for cached layer build
COPY pyproject.toml uv.lock ./

# Install dependencies into /app/.venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Stage 2: Runtime image
FROM python:3.14-slim

# Install runtime utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy uv binary to runtime container
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Ensure virtual environment binaries are on PATH
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy pre-built virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY . /app

# Expose the Django default port
EXPOSE 8000

# Run Django development server listening on all interfaces
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
