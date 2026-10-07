# ==============================================================================
# Base stage: Python base setup and uv installation
# ==============================================================================
FROM python:3.12-slim-bookworm AS base

# Install uv directly from the official prebuilt binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=120

WORKDIR /app

# ==============================================================================
# Builder stage: Install project dependencies into virtual environment
# ==============================================================================
FROM base AS builder

COPY pyproject.toml uv.lock ./

# Install only production dependencies into /app/.venv using uv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ==============================================================================
# Development stage: Live code reload with volume mounting
# ==============================================================================
FROM base AS development

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY . /app
RUN chmod +x /app/scripts/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["granian", "--interface", "asgi", "--loop", "uvloop", "--host", "0.0.0.0", "--port", "8000", "--reload", "app.main:app"]

# ==============================================================================
# Production stage: Minimal, hardened, secure runtime container
# ==============================================================================
FROM python:3.12-slim-bookworm AS production

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Non-root user for security best practices
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/sh -M appuser

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Copy application source code, migrations, and entrypoint
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
COPY --chown=appuser:appuser scripts ./scripts
RUN chmod +x ./scripts/entrypoint.sh

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["granian", "--interface", "asgi", "--loop", "uvloop", "--host", "0.0.0.0", "--port", "8000", "app.main:app"]
