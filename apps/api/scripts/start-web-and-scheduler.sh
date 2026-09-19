#!/usr/bin/env bash
#
# The storefront `api` container runs TWO processes: the HTTP worker and the
# background-scheduler sibling. They used to be one — uvicorn's single event
# loop served requests AND ran every sweep — and a heavy aggregator tick then
# starved checkout for tens of seconds and, under DB-pool exhaustion, dropped an
# order's commit on a reaped connection while the customer saw success
# (MM-20260919-007). Splitting them into two OS processes gives each its own
# event loop and its own DB pools, so a sweep can no longer touch a web request.
#
# Kept in one container (rather than a new service) so the blue/green cutover,
# RAM budget and deploy are unchanged — the box is a 2 GB VM with no room for a
# second always-on API image.
#
# Which process runs the loops is decided HERE, per child, not by the container's
# shared env: the HTTP worker gets STOREFRONT_SCHEDULER_ENABLED off (so
# `app.main`'s lifespan starts none) and the scheduler gets it on. The register
# (`pos-api`) does not use this script and never runs the loops.
#
# If EITHER child exits, the whole container exits so Docker (`restart: always`)
# restarts it — a dead scheduler must never be invisible behind a still-healthy
# HTTP port, which is the one failure a naive two-process container hides.
set -uo pipefail

term() {
  # Forward compose's SIGTERM to both children for a graceful drain.
  kill -TERM "${sched_pid:-}" "${web_pid:-}" 2>/dev/null || true
}
trap term TERM INT

STOREFRONT_SCHEDULER_ENABLED=true python -m app.scheduler_runner &
sched_pid=$!

STOREFRONT_SCHEDULER_ENABLED=false uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips='*' \
  --workers "${UVICORN_WORKERS:-1}" --log-level warning \
  --timeout-graceful-shutdown 8 &
web_pid=$!

# Wait for whichever child exits first, capture its status, then bring the other
# down and exit non-zero so the container is recreated rather than limping on
# with only half of itself alive.
wait -n
code=$?
echo "start-web-and-scheduler: a child exited (status ${code}); stopping the container" >&2
term
wait || true
exit "${code}"
