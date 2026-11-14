#!/bin/sh
# Wait for Postgres, apply migrations (web container only), then exec the command.
set -e

python - <<'PY'
import os, sys, time
import psycopg

url = os.environ.get("DATABASE_URL", "")
for attempt in range(30):
    try:
        psycopg.connect(url, connect_timeout=2).close()
        break
    except psycopg.OperationalError:
        print("Waiting for database...", flush=True)
        time.sleep(1)
else:
    sys.exit("Database not reachable")
PY

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
