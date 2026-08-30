#!/usr/bin/env bash
# Manual deploy for Melting Moments Ecommerce.
#
# Pulls main, migrates, then sequential blue-green cutover behind nginx.
# GitHub Actions (deploy.yml / rollback.yml) call scripts/cutover-backend.sh
# directly after they have already pulled the image and run migrations —
# do not add a stop/start or --force-recreate path here.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/melting-moments-cakes}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

cd "$DEPLOY_DIR"

echo "==> Pulling latest code..."
git fetch origin main
git reset --hard origin/main

echo "==> Restoring live nginx upstreams (git reset restores the committed default)..."
bash scripts/cutover-backend.sh restore-upstreams

echo "==> Pulling API image..."
docker pull ghcr.io/hussu97/mm-ecommerce-api:latest

echo "==> Backing up the database..."
bash scripts/backup-db.sh

echo "==> Running database migrations..."
docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T api alembic upgrade head < /dev/null

echo "==> Cutting over API then POS behind nginx..."
exec bash scripts/cutover-backend.sh
