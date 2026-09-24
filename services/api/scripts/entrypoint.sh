#!/usr/bin/env bash
set -e

# Run database migrations with Alembic on container start if enabled (default: false to prevent race conditions on scale)
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "Applying database migrations (alembic upgrade head)..."
    alembic upgrade head
    echo "Database migrations applied successfully."
fi

# Execute main process (e.g. Granian ASGI server)
exec "$@"
