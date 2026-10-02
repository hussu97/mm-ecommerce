#!/bin/bash
# Setup for a Claude Code cloud environment that debugs prod (docs/claude-cloud-env.md).
#
# This repo is public: nothing here may hold a credential or a private identifier.
# Everything sensitive comes from the environment's variables:
#   MM_VM_SSH_KEY_B64   base64 private SSH key for the VM login user
#   MM_GCS_SA_KEY_B64   base64 service-account JSON (project + account are read from it)
#   MM_VM_USER          VM login user
#   MM_VM_NAME, MM_VM_ZONE   optional, default to the prod VM
# The VM's SSH host key is fetched from Google (guest attributes), not pinned here.
#
# SSH rides Google's IAP TCP tunnel over 443 (direct port 22 is blocked in the
# sandbox), so the VM is addressed by name and needs no public SSH exposure.
#
# Nothing below reads the MM_* variables at setup time: the helpers read them
# when they run, so a session always uses its current values.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
step() { echo "[mm-setup] $*"; }
# Write stdin to an executable path. mv replaces a symlink itself rather than
# writing through it: the image symlinks /usr/local/bin/gcloud into the SDK, and
# `cat >` there would overwrite the SDK's own launcher.
put() { local f=$1 t; t=$(mktemp "$f.XXXX"); cat > "$t"; chmod 755 "$t"; mv -f "$t" "$f"; }

step "apt packages"
timeout 120 apt-get update -qq >/dev/null 2>&1 || step "apt update slow/failed, continuing"
timeout 180 apt-get install -y -qq openssh-client postgresql-client jq >/dev/null 2>&1 || step "apt install failed, continuing"

# --- gcloud SDK: reuse the image's, download only if there is none -----------
step "gcloud SDK"
SDK=""
for c in "$(dirname "$(dirname "$(readlink -f "$(command -v gcloud 2>/dev/null || echo /x/x/x)")")")" \
         /opt/google-cloud-sdk /usr/lib/google-cloud-sdk /usr/share/google-cloud-sdk; do
  [ -f "$c/lib/gcloud.py" ] && { SDK=$c; break; }
done
if [ -z "$SDK" ]; then
  step "no SDK in the image, downloading"
  arch=$(uname -m); [ "$arch" = aarch64 ] && arch=arm
  timeout 240 bash -c "curl -fsSL https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-${arch}.tar.gz | tar -xz -C /opt"
  SDK=/opt/google-cloud-sdk
fi
PY="$SDK/platform/bundledpythonunix/bin/python3"
timeout 20 "$PY" -c 'import ssl' >/dev/null 2>&1 || PY=$(command -v python3)
printf 'SDK=%s\nPY=%s\n' "$SDK" "$PY" > /etc/mm-gcloud
# Repair a launcher an earlier version of this script overwrote through the symlink.
if grep -q 'mm-gcloud-wrapper' "$SDK/bin/gcloud" 2>/dev/null; then
  step "repairing $SDK/bin/gcloud"
  printf '#!/bin/sh\nexec "%s" "%s/lib/gcloud.py" "$@"\n' "$PY" "$SDK" | put "$SDK/bin/gcloud"
fi
step "SDK $SDK, python $PY"

step "uv / pnpm"
command -v uv >/dev/null || timeout 90 bash -c 'curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh' >/dev/null 2>&1 || step "uv install failed, continuing"
command -v pnpm >/dev/null || timeout 90 npm install -g pnpm@9 >/dev/null 2>&1 || true

# --- helpers: thin shims that run bin/ from the session's own checkout ----------
# Cloud environments snapshot the filesystem after setup and skip this script in
# later sessions (until the setup-script text changes or ~7 days pass), so
# anything written here goes stale. The shims only locate scripts/claude-cloud/bin
# in the session's fresh clone and exec it: a fix on main is live next session.
echo "$(cd "$HERE/../.." && pwd)" > /etc/mm-cloud-repo
put /usr/local/bin/mm-cloud-bin <<'EOF'
#!/bin/bash
# Print the scripts/claude-cloud/bin dir of the current mm-ecommerce checkout.
for d in "$(cat /etc/mm-cloud-repo 2>/dev/null)" "$PWD" /home/user/mm-ecommerce; do
  [ -n "$d" ] && [ -d "$d/scripts/claude-cloud/bin" ] && { echo "$d/scripts/claude-cloud/bin"; exit 0; }
done
for d in "$PWD" "$HOME" /home/user /workspace; do
  f=$(find "$d" -maxdepth 4 -type d -path '*/scripts/claude-cloud/bin' -not -path '*/node_modules/*' 2>/dev/null | head -1)
  [ -n "$f" ] && { echo "$f"; exit 0; }
done
exit 1
EOF
for n in mm mmsql mmlogs mm-auth mm-diag; do
  printf '#!/bin/bash\n# mm-cloud-shim\nb=$(mm-cloud-bin) || { echo "%s: no mm-ecommerce checkout found; start the session on that repo" >&2; exit 1; }\nexec bash "$b/%s" "$@"\n' "$n" "$n" | put "/usr/local/bin/$n"
done
# gcloud has to work even without the checkout, so its fallback is inline:
# drop the sandbox's placeholder token and run the SDK directly.
put /usr/local/bin/gcloud <<'EOF'
#!/bin/bash
# mm-gcloud-wrapper (shim)
if b=$(mm-cloud-bin 2>/dev/null); then exec bash "$b/gcloud" "$@"; fi
unset CLOUDSDK_AUTH_ACCESS_TOKEN CLOUDSDK_AUTH_ACCESS_TOKEN_FILE
. /etc/mm-gcloud; exec "$PY" "$SDK/lib/gcloud.py" "$@"
EOF

seen=""; for v in MM_VM_USER MM_VM_SSH_KEY_B64 MM_GCS_SA_KEY_B64; do [ -n "${!v:-}" ] && seen="$seen $v"; done
step "env vars visible to setup:${seen:- none} (the helpers read them at run time either way)"
step "done: mm, mmsql, mmlogs, mm-diag -> $(mm-cloud-bin || echo "checkout not found")"
