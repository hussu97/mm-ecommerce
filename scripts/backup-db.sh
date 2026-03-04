#!/usr/bin/env bash
# Backup PostgreSQL database with 7-day local retention.
# Optionally uploads to S3-compatible storage.
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

# Upload to S3 if configured
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  echo "==> Uploading to S3: s3://${BACKUP_S3_BUCKET}/backups/"
  AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID}" \
  AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY}" \
  aws s3 cp "$BACKUP_FILE" \
    "s3://${BACKUP_S3_BUCKET}/backups/$(basename "$BACKUP_FILE")" \
    --region "${BACKUP_S3_REGION:-us-east-1}"
  echo "    S3 upload complete."
fi

echo "==> Removing backups older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -name "mm_ecommerce_*.sql.gz" \
  -mtime +"${RETENTION_DAYS}" -delete

echo "==> Backup complete."
echo "    Existing local backups:"
ls -lh "$BACKUP_DIR"/mm_ecommerce_*.sql.gz 2>/dev/null || echo "    (none)"
