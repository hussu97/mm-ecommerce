#!/usr/bin/env bash
# One-shot operator script: remove leftover mm-aggregator-automation from the
# prod VM (folder, unused Docker volume, leftover Let's Encrypt cert, and the
# separate mm_aggregator database if it has reappeared).
#
# Run on mm-backend (or scp a copy and ssh). Safe to re-run.
#
# This script never issues DROP TABLE. The only DROP it can run is
# `DROP DATABASE mm_aggregator`, and only after pg_database confirms that
# exact name. Live mm_ecommerce aggregator_* tables, /etc/cron.d/aggregator-warm,
# /opt/melting-moments-cakes, and the ecommerce compose project are keep-list.
set -euo pipefail

LIVE_DIR="/opt/melting-moments-cakes"
LEGACY_DIR="/opt/mm-aggregator-automation"
LEGACY_VOLUME="mm-aggregator-automation_aggregator_output"
LEGACY_CERT="aggregator-api.meltingmomentscakes.com"
LEGACY_COMPOSE_PROJECT="mm-aggregator-automation"
KEEP_VOLUME="melting-moments-cakes_aggregator_sessions"
WARM_CRON="/etc/cron.d/aggregator-warm"
COMPOSE_FILE="docker-compose.prod.yml"
# Literal. Never interpolate POSTGRES_DB (or anything else) into a DROP.
DROP_DATABASE_NAME="mm_aggregator"

abort() {
  echo "ABORT: $*" >&2
  exit 1
}

env_get() {
  # KEY=value only. Do not source .env — APNS_KEY_P8 is a multi-line PEM.
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "${LIVE_DIR}/.env" | head -1 || true)"
  printf '%s' "${line#*=}"
}

echo "==> Legacy aggregator decommission"
echo "    host: $(hostname -s 2>/dev/null || hostname)"
echo "    when: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

[[ -d "$LIVE_DIR" ]] || abort "live deploy dir ${LIVE_DIR} is missing — wrong host?"
[[ -f "${LIVE_DIR}/${COMPOSE_FILE}" ]] || abort "${COMPOSE_FILE} missing under ${LIVE_DIR}"
[[ -f "${LIVE_DIR}/.env" ]] || abort "${LIVE_DIR}/.env missing"

cd "$LIVE_DIR"

POSTGRES_USER="$(env_get POSTGRES_USER)"
POSTGRES_DB="$(env_get POSTGRES_DB)"
[[ -n "$POSTGRES_USER" ]] || abort "POSTGRES_USER missing from .env"
[[ "$POSTGRES_DB" == "mm_ecommerce" ]] || abort "POSTGRES_DB is '${POSTGRES_DB}', expected mm_ecommerce — refusing to talk to postgres"
[[ "$DROP_DATABASE_NAME" == "mm_aggregator" ]] || abort "drop name drifted off mm_aggregator"
[[ "$DROP_DATABASE_NAME" != "mm_ecommerce" ]] || abort "refusing to drop mm_ecommerce"
[[ "$LEGACY_DIR" == "/opt/mm-aggregator-automation" ]] || abort "legacy dir drifted"
[[ "$LEGACY_DIR" != "$LIVE_DIR" ]] || abort "legacy dir resolved to the live deploy dir"
[[ "$LEGACY_CERT" == "aggregator-api.meltingmomentscakes.com" ]] || abort "cert name drifted"
case "$LEGACY_CERT" in
  api.*|pos.*) abort "refusing to touch an api.* or pos.* cert" ;;
esac

# ── Hard guards: leftover app must not be running ──────────────────────────
RUNNING_NAMES="$(docker ps --format '{{.Names}}')"
if printf '%s\n' "$RUNNING_NAMES" | grep -F 'aggregator-api' >/dev/null; then
  echo "$RUNNING_NAMES" | grep -F 'aggregator-api' >&2
  abort "a running container name matches aggregator-api — stop it before decommissioning"
fi

LEGACY_RUNNING="$(docker ps \
  --filter "label=com.docker.compose.project=${LEGACY_COMPOSE_PROJECT}" \
  --format '{{.Names}}')"
if [[ -n "$LEGACY_RUNNING" ]]; then
  echo "$LEGACY_RUNNING" >&2
  abort "compose project ${LEGACY_COMPOSE_PROJECT} is running — stop it before decommissioning"
fi

# ── Keep-list (print before any mutation) ──────────────────────────────────
echo "==> KEEP (must still be here when this script exits)"
echo "    database: ${POSTGRES_DB} (never dropped, never DROP TABLE)"
echo "    live aggregator_* tables:"
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'aggregator_%' ORDER BY 1;" \
  | sed 's/^/      /'
echo "    warm cron: ${WARM_CRON}"
if [[ -e "$WARM_CRON" ]]; then
  echo "      present"
  sed 's/^/      /' "$WARM_CRON" || true
else
  echo "      MISSING — unexpected; not created or removed by this script"
fi
echo "    bootstrap volume: ${KEEP_VOLUME}"
if docker volume inspect "$KEEP_VOLUME" >/dev/null 2>&1; then
  echo "      present"
else
  echo "      MISSING — unexpected; this script does not remove it"
fi
echo "    ecommerce containers:"
docker compose -f "$COMPOSE_FILE" ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}' \
  | sed 's/^/      /'
echo "    live deploy dir: ${LIVE_DIR} (untouched)"
echo

# ── 1. DROP DATABASE mm_aggregator only if pg_database has that exact name ─
echo "==> 1. DROP DATABASE ${DROP_DATABASE_NAME} (only if it exists)"
EXISTING_DB="$(docker compose -f "$COMPOSE_FILE" exec -T postgres \
  psql -U "$POSTGRES_USER" -d postgres -Atc \
  "SELECT datname FROM pg_database WHERE datname = 'mm_aggregator';")"
if [[ -z "$EXISTING_DB" ]]; then
  echo "    not in pg_database — no-op"
elif [[ "$EXISTING_DB" != "mm_aggregator" ]]; then
  abort "pg_database returned '${EXISTING_DB}', not mm_aggregator — refusing DROP DATABASE"
else
  echo "    found mm_aggregator — terminating backends, then DROP DATABASE mm_aggregator"
  docker compose -f "$COMPOSE_FILE" exec -T postgres \
    psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'mm_aggregator' AND pid <> pg_backend_pid();" \
    -c "DROP DATABASE mm_aggregator;"
  echo "    dropped"
fi

# ── 2. Leftover compose volume ─────────────────────────────────────────────
echo "==> 2. docker volume rm ${LEGACY_VOLUME}"
if docker volume inspect "$LEGACY_VOLUME" >/dev/null 2>&1; then
  docker volume rm "$LEGACY_VOLUME"
  echo "    removed"
else
  echo "    not present — no-op"
fi

# ── 3. Leftover aggregator-api cert (existing certbot container) ───────────
# live/archive are 0700 root on the host bind-mount, so detect and delete
# via the running certbot container — never rm an api.* or pos.* cert.
echo "==> 3. certbot delete --cert-name aggregator-api.meltingmomentscakes.com"
if docker compose -f "$COMPOSE_FILE" exec -T certbot \
  test -e /etc/letsencrypt/live/aggregator-api.meltingmomentscakes.com; then
  docker compose -f "$COMPOSE_FILE" exec -T certbot \
    certbot delete --cert-name aggregator-api.meltingmomentscakes.com --non-interactive
  echo "    deleted"
else
  echo "    cert not present — no-op"
fi

echo "    remaining live certs (api.* / pos.* must still be here):"
docker compose -f "$COMPOSE_FILE" exec -T certbot \
  ls -1 /etc/letsencrypt/live | sed 's/^/      /'

# ── 4. Leftover clone (includes a .env with live secrets) ──────────────────
echo "==> 4. rm -rf /opt/mm-aggregator-automation"
if [[ -e "$LEGACY_DIR" ]]; then
  # Clone is mixed-ownership (git files vs docker-created root files). Always
  # go through passwordless sudo so a partial unprivileged rm cannot leave a
  # half-deleted tree and a .env.
  sudo -n rm -rf /opt/mm-aggregator-automation \
    || abort "could not remove /opt/mm-aggregator-automation (need passwordless sudo)"
  echo "    removed"
else
  echo "    not present — no-op"
fi

# ── 5. Explicit non-touches ────────────────────────────────────────────────
echo "==> 5. left untouched by design"
echo "    ${WARM_CRON}"
echo "    ${LIVE_DIR}"
echo "    ${KEEP_VOLUME}"
echo "    ${POSTGRES_DB}.aggregator_*"
echo

# ── Verify ─────────────────────────────────────────────────────────────────
echo "==> Verify"
FAIL=0

if [[ -e "$LEGACY_DIR" ]]; then
  echo "    FAIL: ${LEGACY_DIR} still exists"
  FAIL=1
else
  echo "    ok: folder gone"
fi

if docker volume inspect "$LEGACY_VOLUME" >/dev/null 2>&1; then
  echo "    FAIL: volume ${LEGACY_VOLUME} still exists"
  FAIL=1
else
  echo "    ok: volume gone"
fi

if docker compose -f "$COMPOSE_FILE" exec -T certbot \
  test -e /etc/letsencrypt/live/aggregator-api.meltingmomentscakes.com; then
  echo "    FAIL: aggregator-api cert still in certbot live/"
  FAIL=1
else
  echo "    ok: aggregator-api cert gone"
fi
if docker compose -f "$COMPOSE_FILE" exec -T certbot \
  test -e /etc/letsencrypt/renewal/aggregator-api.meltingmomentscakes.com.conf; then
  echo "    FAIL: aggregator-api renewal conf still present"
  FAIL=1
else
  echo "    ok: aggregator-api renewal conf gone"
fi
for keep_cert in api.meltingmomentscakes.com pos.meltingmomentscakes.com; do
  if docker compose -f "$COMPOSE_FILE" exec -T certbot \
    test -e "/etc/letsencrypt/live/${keep_cert}"; then
    echo "    ok: ${keep_cert} cert still present"
  else
    echo "    FAIL: ${keep_cert} cert missing"
    FAIL=1
  fi
done

STILL_HAS_DROP_DB="$(docker compose -f "$COMPOSE_FILE" exec -T postgres \
  psql -U "$POSTGRES_USER" -d postgres -Atc \
  "SELECT datname FROM pg_database WHERE datname = 'mm_aggregator';")"
if [[ -n "$STILL_HAS_DROP_DB" ]]; then
  echo "    FAIL: database mm_aggregator still in pg_database"
  FAIL=1
else
  echo "    ok: mm_aggregator not in pg_database"
fi

TABLE_COUNT="$(docker compose -f "$COMPOSE_FILE" exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'aggregator_%';")"
if [[ "${TABLE_COUNT}" -ge 1 ]]; then
  echo "    ok: ${TABLE_COUNT} aggregator_* tables still in ${POSTGRES_DB}"
else
  echo "    FAIL: no aggregator_* tables in ${POSTGRES_DB}"
  FAIL=1
fi

if [[ -e "$WARM_CRON" ]]; then
  echo "    ok: warm cron still installed"
else
  echo "    FAIL: ${WARM_CRON} missing"
  FAIL=1
fi

if docker volume inspect "$KEEP_VOLUME" >/dev/null 2>&1; then
  echo "    ok: ${KEEP_VOLUME} still present"
else
  echo "    FAIL: keep volume ${KEEP_VOLUME} missing"
  FAIL=1
fi

if [[ ! -d "$LIVE_DIR" ]]; then
  echo "    FAIL: live deploy dir vanished"
  FAIL=1
else
  echo "    ok: ${LIVE_DIR} still present"
fi

echo "    ecommerce containers:"
docker compose -f "$COMPOSE_FILE" ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}' \
  | sed 's/^/      /'
UNHEALTHY="$(docker compose -f "$COMPOSE_FILE" ps --format '{{.Service}} {{.Status}}' \
  | grep -Ev 'aggregator-worker' \
  | grep -Eiv 'healthy|Up' || true)"
if [[ -n "$UNHEALTHY" ]]; then
  echo "    FAIL: unexpected non-healthy service:"
  echo "$UNHEALTHY" | sed 's/^/      /'
  FAIL=1
else
  echo "    ok: ecommerce containers still healthy"
fi

if [[ "$FAIL" -ne 0 ]]; then
  abort "verification failed"
fi

echo
echo "==> Decommission complete."
