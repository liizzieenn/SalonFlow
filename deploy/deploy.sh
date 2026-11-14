#!/usr/bin/env bash
# Deploy the latest main on the server (run by .github/workflows/deploy.yml over SSH,
# or by hand). The server checkout is deploy-only: local changes are discarded.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.prod.yml)

echo "==> Updating code"
git fetch --prune origin main
git reset --hard origin/main

echo "==> Building images"
"${COMPOSE[@]}" build --pull

echo "==> Starting services (web runs migrations on start)"
"${COMPOSE[@]}" up -d --remove-orphans --wait --wait-timeout 180

echo "==> Django deployment checks"
"${COMPOSE[@]}" exec -T web python manage.py check --deploy --fail-level ERROR

echo "==> Cleaning up old images"
docker image prune -f >/dev/null

echo "==> Deployed $(git rev-parse --short HEAD)"
