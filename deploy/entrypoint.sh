#!/bin/sh
set -e
cd /app
echo "running migrations"
alembic upgrade head
if [ "$(echo "${SEED_DEMO:-false}" | tr '[:upper:]' '[:lower:]')" = "true" ]; then
  echo "SEED_DEMO=true: seeding demo data if the database is empty"
  python -m app.seed --demo
fi
python -m app.seed --bootstrap-admin
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
