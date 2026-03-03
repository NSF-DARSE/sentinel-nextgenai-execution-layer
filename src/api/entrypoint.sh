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

echo "Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000