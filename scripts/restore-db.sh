#!/usr/bin/env bash
# Restore PostgreSQL database from a backup file.
# Usage: ./scripts/restore-db.sh <path-to-backup.sql.gz>
#
# F-OPS-8 rewrite. The old version only stopped `api` before dropping the
# database — `pos-api`, both green slots (if a cutover happened to be
# mid-flight) and the aggregator-worker daemon could all still hold open
# connections and keep writing right up to (and past) the `DROP DATABASE`,
# which either fails outright ("database is being accessed by other users")
# or, worse, races the drop and writes into the window between DROP and
# CREATE. It also had no `ON_ERROR_STOP` on the restore, so a truncated or
# corrupt dump could fail halfway through and `psql` would carry on past the
# error, leaving a database that LOOKS restored (the command exits 0) but is
# missing whatever came after the failure — and nothing checked the result
# against what a real mm_ecommerce database should contain.
set -euo pipefail

BACKUP_FILE="${1:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/melting-moments-cakes}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

if [ -z "$BACKUP_FILE" ]; then
  echo "Usage: $0 <path-to-backup.sql.gz>"
  echo ""
  echo "Available backups:"
  ls -lh "$DEPLOY_DIR/backups"/mm_ecommerce_*.sql.gz 2>/dev/null || echo "  (none found)"
  exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
  echo "ERROR: Backup file not found: $BACKUP_FILE"
  exit 1
fi

cd "$DEPLOY_DIR"

# Load env vars for DB credentials. `set -e` is deliberately lifted around the
# source, same reason as scripts/backup-db.sh: `.env` holds APNS_KEY_P8, a
# multi-line PEM, and sourcing it makes bash try to execute
# `-----BEGIN PRIVATE KEY-----` as a command (harmless — the real assignments
# either side still happen — but it exits 127, and under `set -e` that would
# kill this script before it asked a single question).
set +e
set -o allexport
# shellcheck disable=SC1091
source .env
set +o allexport
set -e

: "${POSTGRES_USER:?POSTGRES_USER missing from .env — cannot restore}"
: "${POSTGRES_DB:?POSTGRES_DB missing from .env — cannot restore}"

# `--profile green` so `ps`/`stop` see api-green/pos-api-green even though
# neither is in the default `up` set — same reason cutover-backend.sh always
# passes it.
_compose() {
  docker compose -f "$COMPOSE_FILE" --profile green "$@" < /dev/null
}

_running() {
  local id
  id=$(_compose ps -q --status running "$1" 2>/dev/null || true)
  [ -n "$id" ]
}

echo "WARNING: This will drop and recreate the '${POSTGRES_DB}' database."
echo "Backup file: $BACKUP_FILE"
read -r -p "Are you sure? [y/N] " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

# Every service that can hold a connection to mm_ecommerce and write to it.
# Both green slots are included even though neither is normally running —
# restoring mid-cutover is unusual, but "unusual" is exactly when a script
# like this gets run, and a color left running would keep writing through
# the drop. Postgres and Redis are deliberately NOT in this list: they are
# what is being restored, and what feeds the healthchecks the rest of this
# script uses.
WRITERS=(api pos-api api-green pos-api-green aggregator-worker)

# Remember which of them were actually running BEFORE we touch anything, so
# the restart at the end brings back exactly what was up — including,
# specifically, NOT starting a green slot that was correctly stopped, which
# would leave two colours live behind nginx.
WAS_RUNNING=()
for svc in "${WRITERS[@]}"; do
  if _running "$svc"; then
    WAS_RUNNING+=("$svc")
  fi
done

echo "==> Stopping every writer (${WRITERS[*]}) to prevent writes during restore..."
_compose stop "${WRITERS[@]}"

echo "==> Terminating any remaining backend connections to ${POSTGRES_DB}..."
# Belt and braces on top of the stop above: a lingering psql session, a
# healthcheck mid-flight, or a connection the pool has not yet closed can
# still hold the database open, and `DROP DATABASE` refuses outright
# ("database ... is being accessed by other users") rather than waiting.
# `pg_terminate_backend` against everything but this very connection clears
# them so the drop cannot silently race a writer that never actually stopped.
_compose exec -T postgres \
  psql -U "${POSTGRES_USER}" postgres -v ON_ERROR_STOP=1 -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();"

echo "==> Dropping and recreating database..."
_compose exec -T postgres \
  psql -U "${POSTGRES_USER}" postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS ${POSTGRES_DB};" \
  -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"

echo "==> Restoring from backup..."
# `ON_ERROR_STOP=1` is load-bearing: without it psql's default behaviour on a
# SQL error inside the dump is to print it and keep going, which turns "the
# dump was truncated" or "one statement failed" into a database that LOOKS
# restored (this command exits 0) but is silently missing whatever came
# after. `set -o pipefail` (from `set -euo pipefail` above) makes the whole
# pipeline's exit status psql's, not gunzip's, so a failure here stops the
# script — deliberately, before the "restart everything" step: a database a
# human needs to look at should stay down, not get restarted on top of.
gunzip -c "$BACKUP_FILE" | \
  _compose exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER}" "${POSTGRES_DB}"

echo "==> Verifying the restore looks like a real mm_ecommerce database..."
# Cheap, specific sanity check: every schema this app has ever run carries an
# `alembic_version` row. Its absence — an empty dump, a backup of the wrong
# database, a `pg_dump` that failed after connecting but before writing any
# tables — is exactly the silent-success case `ON_ERROR_STOP` above does not
# catch on its own (a dump that is valid SQL but simply isn't THIS database
# restores cleanly and leaves nothing behind to complain).
ALEMBIC_VERSION=$(
  _compose exec -T postgres \
    psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tA \
    -c "SELECT version_num FROM alembic_version;" 2>/dev/null \
    | tr -d '[:space:]'
)
if [ -z "$ALEMBIC_VERSION" ]; then
  echo "ERROR: restored database has no alembic_version row — this does not"
  echo "       look like a valid mm_ecommerce backup. Services are being left"
  echo "       STOPPED so nobody serves traffic against it; investigate before"
  echo "       restarting anything by hand."
  exit 1
fi
echo "    alembic_version: ${ALEMBIC_VERSION}"

if [ "${#WAS_RUNNING[@]}" -eq 0 ]; then
  echo "==> Nothing was running before the restore; leaving everything stopped."
else
  echo "==> Restarting what was running before the restore (${WAS_RUNNING[*]})..."
  _compose start "${WAS_RUNNING[@]}"
fi

echo "==> Restore complete."
