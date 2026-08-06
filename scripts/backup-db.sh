#!/usr/bin/env bash
# Backup PostgreSQL database with 7-day local retention.
# Optionally uploads to GCP Cloud Storage (uses VM attached service account).
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/melting-moments-cakes}"
BACKUP_DIR="${BACKUP_DIR:-$DEPLOY_DIR/backups}"
COMPOSE_FILE="docker-compose.prod.yml"
RETENTION_DAYS=7

cd "$DEPLOY_DIR"

# Load env vars for credentials + optional S3 config.
#
# `set -e` is deliberately lifted around the source. `.env` holds APNS_KEY_P8,
# which is a multi-line PEM, so sourcing it makes bash try to run
# `-----BEGIN PRIVATE KEY-----` as a command. That is harmless — the assignments
# either side still happen — but it exits 127, and under `set -e` that killed
# this script before it took a single backup. It failed silently for as long as
# the script existed, because nothing ran it automatically.
#
# The variables that matter are checked below instead, which is a better test
# than the exit status of sourcing a file full of secrets.
set +e
set -o allexport
# shellcheck disable=SC1091
source .env
set +o allexport
set -e

: "${POSTGRES_USER:?POSTGRES_USER missing from .env — cannot back up}"
: "${POSTGRES_DB:?POSTGRES_DB missing from .env — cannot back up}"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/mm_ecommerce_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "==> Creating database backup: $BACKUP_FILE"
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
  | gzip > "$BACKUP_FILE"

echo "    Backup size: $(du -sh "$BACKUP_FILE" | cut -f1)"

# Upload to GCS if configured (VM must have an attached service account with
# Storage Object Creator role).
#
# Best-effort, deliberately. The local dump is what protects a migration; the
# offsite copy protects against losing the VM, which is a different and slower
# emergency. Letting the upload fail the script meant a missing CLI took the
# whole backup with it — `gsutil` is not installed on this VM at all, and
# `BACKUP_GCS_BUCKET` is set, so under `set -e` every run died here at exit 127
# *after* writing a perfectly good local backup it then disowned.
#
# `gcloud storage` is preferred and `gsutil` is the fallback; neither present
# is a warning, not a failure.
if [ -n "${BACKUP_GCS_BUCKET:-}" ]; then
  GCS_TARGET="gs://${BACKUP_GCS_BUCKET}/backups/$(basename "$BACKUP_FILE")"
  echo "==> Uploading to GCS: $GCS_TARGET"
  if command -v gcloud >/dev/null 2>&1; then
    gcloud storage cp "$BACKUP_FILE" "$GCS_TARGET" \
      && echo "    GCS upload complete." \
      || echo "    WARNING: GCS upload failed. The local backup is still in $BACKUP_DIR."
  elif command -v gsutil >/dev/null 2>&1; then
    gsutil cp "$BACKUP_FILE" "$GCS_TARGET" \
      && echo "    GCS upload complete." \
      || echo "    WARNING: GCS upload failed. The local backup is still in $BACKUP_DIR."
  else
    echo "    WARNING: BACKUP_GCS_BUCKET is set but neither gcloud nor gsutil is"
    echo "             installed on this host, so backups are LOCAL ONLY."
  fi
fi

echo "==> Removing backups older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -name "mm_ecommerce_*.sql.gz" \
  -mtime +"${RETENTION_DAYS}" -delete

echo "==> Backup complete."
echo "    Existing local backups:"
ls -lh "$BACKUP_DIR"/mm_ecommerce_*.sql.gz 2>/dev/null || echo "    (none)"
