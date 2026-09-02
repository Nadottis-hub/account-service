#!/bin/sh
set -e

# Railway runs a single instance per deploy, so migrating on boot is safe here.
# Split this into a release step if this service is ever scaled to multiple replicas.
echo "running database migrations..."
alembic upgrade head

echo "starting account-service on port ${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
