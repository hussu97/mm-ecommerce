#!/usr/bin/env bash
# Backup PostgreSQL database with 7-day local retention.
# Optionally uploads to GCP Cloud Storage (uses VM attached service account).
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/mm-ecommerce}"
BACKUP_DIR="${BACKUP_DIR:-$DEPLOY_DIR/backups}"
COMPOSE_FILE="docker-compose.prod.yml"
RETENTION_DAYS=7

cd "$DEPLOY_DIR"

# Load env vars for credentials + optional S3 config
set -o allexport
# shellcheck disable=SC1091
source .env
set +o allexport

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/mm_ecommerce_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "==> Creating database backup: $BACKUP_FILE"
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
  | gzip > "$BACKUP_FILE"

echo "    Backup size: $(du -sh "$BACKUP_FILE" | cut -f1)"

# Upload to GCS if configured (VM must have an attached service account with Storage Object Creator role)
if [ -n "${BACKUP_GCS_BUCKET:-}" ]; then
  echo "==> Uploading to GCS: gs://${BACKUP_GCS_BUCKET}/backups/"
  gsutil cp "$BACKUP_FILE" \
    "gs://${BACKUP_GCS_BUCKET}/backups/$(basename "$BACKUP_FILE")"
  echo "    GCS upload complete."
fi

echo "==> Removing backups older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -name "mm_ecommerce_*.sql.gz" \
  -mtime +"${RETENTION_DAYS}" -delete

echo "==> Backup complete."
echo "    Existing local backups:"
ls -lh "$BACKUP_DIR"/mm_ecommerce_*.sql.gz 2>/dev/null || echo "    (none)"
