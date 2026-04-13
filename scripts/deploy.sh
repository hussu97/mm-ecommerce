#!/usr/bin/env bash
# Zero-downtime redeploy for Melting Moments Ecommerce.
# Pulls latest code, rebuilds changed images, and restarts services.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/melting-moments-cakes}"
COMPOSE_FILE="docker-compose.prod.yml"

cd "$DEPLOY_DIR"

echo "==> Pulling latest code..."
git pull origin main

echo "==> Building API image..."
docker compose -f "$COMPOSE_FILE" build api

echo "==> Restarting API with new image..."
docker compose -f "$COMPOSE_FILE" up -d --no-deps api

echo "==> Waiting for API to become healthy..."
API_READY=false
for i in $(seq 1 30); do
  if docker compose -f "$COMPOSE_FILE" exec -T api curl -sf http://localhost:8000/ping > /dev/null 2>&1; then
    echo "API is up."
    API_READY=true
    break
  fi
  echo "  waiting... (${i}/30)"
  sleep 5
done

if [ "$API_READY" != "true" ]; then
  echo "ERROR: API failed to start. Container logs:"
  docker compose -f "$COMPOSE_FILE" logs --tail=50 api
  exit 1
fi

echo "==> Running database migrations..."
docker compose -f "$COMPOSE_FILE" exec -T api alembic upgrade head

echo "==> Ensuring nginx is up..."
docker compose -f "$COMPOSE_FILE" up -d --no-deps nginx
docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload || true

echo "==> Pruning unused images..."
docker image prune -f

echo ""
echo "==> Deployment complete!"
docker compose -f "$COMPOSE_FILE" ps
