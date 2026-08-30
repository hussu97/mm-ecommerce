#!/usr/bin/env bash
# Backup PostgreSQL database, keeping the newest KEEP_BACKUPS local dumps.
# Optionally uploads to GCP Cloud Storage (uses VM attached service account).
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/melting-moments-cakes}"
BACKUP_DIR="${BACKUP_DIR:-$DEPLOY_DIR/backups}"
COMPOSE_FILE="docker-compose.prod.yml"
# Retention is COUNT-based, not age-based: keep the newest KEEP_BACKUPS dumps
# whenever they were taken, prune everything older. A deploy that happens to fire
# several times in a day still leaves exactly the last KEEP_BACKUPS on disk (age
# retention would keep every dump for 7 days, then all of them expire together);
# a quiet week still leaves the last KEEP_BACKUPS (age retention would delete the
# only dumps we have because they aged out). This prune runs every deploy, right
# after this run's fresh dump lands, so the newest is always among those kept.
KEEP_BACKUPS="${KEEP_BACKUPS:-5}"

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
#
# The SDK is found before that check rather than trusted to be on `PATH`. It is
# installed in a user's home directory here, and put on `PATH` by a line in
# `.bashrc` — which a non-interactive `ssh host 'command'` never sources. So the
# deploy, which runs exactly that way, saw no `gcloud`, took the LOCAL ONLY
# branch and printed a warning into a log nobody reads. Between that and a
# bucket that had never actually been created, this script had never once put a
# backup anywhere but the same disk as the database it was dumping.
for _sdk_bin in \
  /usr/lib/google-cloud-sdk/bin \
  /usr/local/google-cloud-sdk/bin \
  /snap/bin \
  "$HOME/google-cloud-sdk/bin" \
  /home/*/google-cloud-sdk/bin
do
  if [ -x "$_sdk_bin/gcloud" ]; then
    case ":$PATH:" in
      *":$_sdk_bin:"*) ;;
      *) PATH="$PATH:$_sdk_bin" ;;
    esac
    break
  fi
done
export PATH

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

echo "==> Pruning local backups, keeping the newest ${KEEP_BACKUPS}..."
# Count-based prune. Read newest-first (`ls -t`) into an array with a while-read
# loop (not `mapfile`, which is a bash-4 builtin absent from the bash 3.2 some
# hosts ship). Fed by a process substitution, not a pipe, so the `|| true`
# swallows the non-zero `ls` exit when the glob matches nothing and `set -o
# pipefail` never sees a SIGPIPE — the same trap the diagnostics block below
# documents. Our filenames are timestamped and contain no newlines, so line-based
# reading is safe. Everything past KEEP_BACKUPS is deleted; the dump this run just
# wrote is the newest, so it is always kept.
_backups=()
while IFS= read -r _f; do
  [ -n "$_f" ] && _backups+=("$_f")
done < <(ls -t "$BACKUP_DIR"/mm_ecommerce_*.sql.gz 2>/dev/null || true)
if [ "${#_backups[@]}" -gt "$KEEP_BACKUPS" ]; then
  for _old in "${_backups[@]:$KEEP_BACKUPS}"; do
    rm -f -- "$_old" && echo "    removed $(basename "$_old")"
  done
fi

echo "==> Backup complete."
# A full ls of every retained dump is hundreds of log lines on this box and
# nobody reads it. Count + newest is enough to see the job ran.
#
# The `|| true` is load-bearing under this script's `set -euo pipefail`: once a
# SECOND backup exists, `head -1` closes the pipe after one line, `ls` is killed
# by SIGPIPE (exit 141), `pipefail` makes the whole substitution non-zero, and
# `set -e` then kills the script HERE — after a perfect backup and the "Backup
# complete" line, but before the exit-0 below. The deploy runs
# `backup-db.sh || { echo "backup failed"; exit 1; }`, so a good backup aborted
# the whole migration. This is diagnostics; its exit status must never fail the
# backup.
newest=$(ls -t "$BACKUP_DIR"/mm_ecommerce_*.sql.gz 2>/dev/null | head -1 || true)
count=$(find "$BACKUP_DIR" -name "mm_ecommerce_*.sql.gz" 2>/dev/null | wc -l | tr -d " " || true)
echo "    ${count:-?} local backups; newest: ${newest:-none}"
