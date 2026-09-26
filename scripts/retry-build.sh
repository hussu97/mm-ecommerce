#!/usr/bin/env bash
#
# Run a Next.js build up to three times.
#
# Turbopack fetches Google Fonts during the build (next/font/google), and that
# fetch fails intermittently with "next/font/google queries have exactly one
# entry" / "Can't resolve '@vercel/turbopack-next/internal/font/google/font'".
# Nothing is wrong with the code — the same commit builds on the next attempt —
# but one bad response used to fail the whole deploy job and leave web or admin
# on the previous release until someone re-ran it by hand. A build has no side
# effects until the separate deploy step uploads it, so retrying is safe.
#
# Usage: scripts/retry-build.sh <build command...>
set -uo pipefail

for attempt in 1 2 3; do
  if "$@"; then
    exit 0
  fi
  echo "::warning::build attempt ${attempt} of 3 failed: $*"
  sleep $((attempt * 15))
done
echo "::error::build failed three times: $*"
exit 1
