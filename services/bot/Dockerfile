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

EXPOSE 8080

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["python", "-m", "bot.main"]

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

# Copy application source code and entrypoint
COPY --chown=appuser:appuser bot ./bot
COPY --chown=appuser:appuser scripts ./scripts
RUN chmod +x ./scripts/entrypoint.sh

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"WEB_SERVER_PORT\", \"8080\")}/health') if os.getenv('TELEGRAM_BOT_MODE') == 'webhook' else exit(0)" || exit 1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["python", "-m", "bot.main"]
