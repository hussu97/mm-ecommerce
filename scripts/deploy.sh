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
for i in $(seq 1 12); do
  if docker compose -f "$COMPOSE_FILE" exec -T api sh -c 'curl -sf http://localhost:8000/health' 2>/dev/null; then
    echo "API is up."
    break
  fi
  echo "  waiting... (${i}/12)"
  sleep 5
done

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
