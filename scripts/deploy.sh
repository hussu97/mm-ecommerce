#!/usr/bin/env bash
# Zero-downtime redeploy for Melting Moments Ecommerce.
# Pulls latest code, rebuilds changed images, and restarts services.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/mm-ecommerce}"
COMPOSE_FILE="docker-compose.prod.yml"

cd "$DEPLOY_DIR"

echo "==> Pulling latest code..."
git pull origin main

echo "==> Building and restarting API service..."
# Build new image first (keeps old container running)
docker compose -f "$COMPOSE_FILE" build api

echo "==> Restarting API with new image..."
docker compose -f "$COMPOSE_FILE" up -d --no-deps api
sleep 10

echo "==> Running database migrations..."
docker compose -f "$COMPOSE_FILE" exec -T api alembic upgrade head

echo "==> Reloading nginx..."
docker compose -f "$COMPOSE_FILE" exec nginx nginx -s reload

echo "==> Pruning unused images..."
docker image prune -f

echo ""
echo "==> Deployment complete!"
docker compose -f "$COMPOSE_FILE" ps
