#!/bin/bash
# Paste this into the cloud environment's "Setup script" box (claude.ai/code).
# It runs after the repo is cloned and hands off to the committed script, so
# updates ship as commits instead of edits in the UI.
set -euo pipefail
for d in "$PWD" "$HOME" /home/user /workspace /root; do
  f=$(find "$d" -maxdepth 4 -path '*/scripts/claude-cloud/setup.sh' -not -path '*/node_modules/*' 2>/dev/null | head -1)
  [ -n "$f" ] && exec bash "$f"
done
echo "scripts/claude-cloud/setup.sh not found: start the session on the mm-ecommerce repo" >&2
exit 1
