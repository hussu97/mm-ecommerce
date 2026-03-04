#!/usr/bin/env bash
# Restore PostgreSQL database from a backup file.
# Usage: ./scripts/restore-db.sh <path-to-backup.sql.gz>
set -euo pipefail

BACKUP_FILE="${1:-}"

if [ -z "$BACKUP_FILE" ]; then
  echo "Usage: $0 <path-to-backup.sql.gz>"
  echo ""
  echo "Available backups:"
  ls -lh "${DEPLOY_DIR:-/opt/mm-ecommerce}/backups"/mm_ecommerce_*.sql.gz 2>/dev/null || echo "  (none found)"
  exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
  echo "ERROR: Backup file not found: $BACKUP_FILE"
  exit 1
fi

DEPLOY_DIR="${DEPLOY_DIR:-/opt/mm-ecommerce}"
COMPOSE_FILE="docker-compose.prod.yml"

cd "$DEPLOY_DIR"

# Load env vars
set -o allexport
# shellcheck disable=SC1091
source .env
set +o allexport

echo "WARNING: This will drop and recreate the '${POSTGRES_DB}' database."
echo "Backup file: $BACKUP_FILE"
read -r -p "Are you sure? [y/N] " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

echo "==> Stopping API to prevent writes..."
docker compose -f "$COMPOSE_FILE" stop api

echo "==> Dropping and recreating database..."
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  psql -U "${POSTGRES_USER}" postgres \
  -c "DROP DATABASE IF EXISTS ${POSTGRES_DB};" \
  -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"

echo "==> Restoring from backup..."
gunzip -c "$BACKUP_FILE" | \
  docker compose -f "$COMPOSE_FILE" exec -T postgres \
  psql -U "${POSTGRES_USER}" "${POSTGRES_DB}"

echo "==> Restarting API..."
docker compose -f "$COMPOSE_FILE" start api

echo "==> Restore complete."
