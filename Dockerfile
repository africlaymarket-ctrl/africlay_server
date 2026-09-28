# Stage 1: Build virtual environment with uv
FROM python:3.14-slim AS builder

# Install uv binary from official Astral image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy dependency specifications for cached layer build
COPY pyproject.toml uv.lock ./

# Install dependencies into /opt/venv
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

# Ensure virtual environment is on PATH and recognized by uv
ENV PATH="/opt/venv/bin:$PATH" \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy pre-built virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Fail the image build if the frozen dependency set does not include Gunicorn.
RUN test -x /opt/venv/bin/gunicorn

# Copy application code
COPY . /app

# Render sets PORT at runtime. Keep 8000 as a fallback for local Docker use.
EXPOSE 8000

# Run the production WSGI server and honor the platform-provided port.
CMD ["sh", "-c", "exec /opt/venv/bin/gunicorn africlay_server.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120"]
