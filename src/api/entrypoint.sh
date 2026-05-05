#!/bin/sh
set -e

echo "Waiting for Postgres to be ready..."

python - <<'PY'
import os, time
from sqlalchemy import create_engine, text

url = os.getenv("DATABASE_URL")
if not url:
    raise SystemExit("DATABASE_URL is not set")

engine = create_engine(url, pool_pre_ping=True)

for i in range(60):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("Postgres is ready.")
        break
    except Exception as e:
        print(f"DB not ready ({i+1}/60): {e}")
        time.sleep(1)
else:
    raise SystemExit("Postgres never became ready.")
PY

echo "Running migrations..."
alembic upgrade head

if [ "$SENTINEL_MODE" = "worker" ]; then
    echo "Starting Worker mode..."
    # Cloud Run requires a process to listen on $PORT. 
    # Start a simple background server to satisfy the health check.
    python3 -m http.server ${PORT:-8080} &
    # Concurrency 1 ensures we stay within 1GB+ memory footprints for LLM/spaCy models.
    # Added --prefetch-multiplier 1 to ensure workers don't grab more tasks than they can handle.
    exec celery -A app.worker.celery_app worker --loglevel=info --concurrency=1 --prefetch-multiplier=1
else
    echo "Starting API mode..."
    exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
fi