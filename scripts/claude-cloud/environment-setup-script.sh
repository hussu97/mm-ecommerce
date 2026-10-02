#!/bin/bash
# Paste this into the cloud environment's "Setup script" box (claude.ai/code).
# It runs after the repo is cloned and hands off to the committed script, so
# updates ship as commits instead of edits in the UI.
#
# The environment snapshots the filesystem after setup and skips this script in
# later sessions until its text changes. The helpers run from the fresh clone, so
# that is fine for script fixes; bump this line only to force a full re-setup
# (new tooling, or to clear state an old setup left behind).
# setup-rev: 2026-10-02.2
set -euo pipefail
for d in "$PWD" "$HOME" /home/user /workspace /root; do
  f=$(find "$d" -maxdepth 4 -path '*/scripts/claude-cloud/setup.sh' -not -path '*/node_modules/*' 2>/dev/null | head -1)
  [ -n "$f" ] && exec bash "$f"
done
echo "scripts/claude-cloud/setup.sh not found: start the session on the mm-ecommerce repo" >&2
exit 1
