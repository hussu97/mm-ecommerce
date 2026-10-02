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

# --- mm-auth: sync creds + ssh config from the CURRENT env, every call ---------
put /usr/local/bin/mm-auth <<'EOF'
#!/bin/bash
set -euo pipefail
d="$HOME/.config/mm"; mkdir -p "$HOME/.ssh" "$d"; chmod 700 "$HOME/.ssh" "$d"
sync() {  # sync <b64 value> <file>: rewrite only when the value changed
  [ -n "$1" ] || return 0
  local t; t=$(mktemp "$2.XXXX"); echo "$1" | base64 -d > "$t"; chmod 600 "$t"
  if cmp -s "$t" "$2"; then rm -f "$t"; else mv -f "$t" "$2"; fi
}
sync "${MM_VM_SSH_KEY_B64:-}" "$HOME/.ssh/mm_vm"
sync "${MM_GCS_SA_KEY_B64:-}" "$d/sa.json"
if [ -s "$d/sa.json" ]; then
  python3 - "$d/sa.json" > "$d/env" <<'PY'
import json, sys
k = json.load(open(sys.argv[1]))
print(f"MM_GCP_PROJECT={k['project_id']}\nMM_GCP_ACCOUNT={k['client_email']}")
PY
fi
vm="${MM_VM_NAME:-mm-backend}"; zone="${MM_VM_ZONE:-me-central1-a}"
cat > "$HOME/.ssh/mm_config" <<CFG
Host mm $vm
  HostName $vm
  HostKeyAlias $vm
  User ${MM_VM_USER:-}
  IdentityFile $HOME/.ssh/mm_vm
  IdentitiesOnly yes
  UserKnownHostsFile $HOME/.ssh/known_hosts_mm
  StrictHostKeyChecking yes
  ServerAliveInterval 30
  ConnectTimeout 30
  ProxyCommand /usr/local/bin/gcloud compute start-iap-tunnel $vm 22 --listen-on-stdin --zone=$zone --verbosity=error
CFG
EOF

# --- gcloud wrapper ------------------------------------------------------------
# The sandbox sets CLOUDSDK_AUTH_ACCESS_TOKEN=proxy-injected (a placeholder for the
# API-credentials feature, which Team plans lack); gcloud prefers it over any
# account, so every call 401s. Drop it and pin the service account.
put /usr/local/bin/gcloud <<'EOF'
#!/bin/bash
# mm-gcloud-wrapper
unset CLOUDSDK_AUTH_ACCESS_TOKEN CLOUDSDK_AUTH_ACCESS_TOKEN_FILE
. /etc/mm-gcloud
mm-auth
d="$HOME/.config/mm"
if [ -s "$d/env" ]; then
  . "$d/env"
  export CLOUDSDK_CORE_ACCOUNT="$MM_GCP_ACCOUNT" CLOUDSDK_CORE_PROJECT="$MM_GCP_PROJECT"
  mark="$d/.activated-$(sha256sum "$d/sa.json" | cut -c1-12)"
  if [ ! -f "$mark" ]; then
    timeout 30 "$PY" "$SDK/lib/gcloud.py" auth activate-service-account \
      --key-file="$d/sa.json" -q >/dev/null 2>&1 && touch "$mark"
  fi
fi
export CLOUDSDK_CORE_CHECK_GCE_METADATA=false CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK=true
export CLOUDSDK_CORE_DISABLE_USAGE_REPORTING=true CLOUDSDK_CORE_DISABLE_PROMPTS=1
exec "$PY" "$SDK/lib/gcloud.py" "$@"
EOF

# --- helpers ------------------------------------------------------------------
# mm <cmd>        run a command on the VM (sudo for docker)
# mmsql "<sql>"   SQL against the prod Postgres container, as its app user (READ-WRITE)
# mmlogs <svc>    a container's logs, e.g. mmlogs api --since 1h
put /usr/local/bin/mm <<'EOF'
#!/bin/bash
set -euo pipefail
mm-auth
miss=""
for v in MM_VM_USER MM_VM_SSH_KEY_B64 MM_GCS_SA_KEY_B64; do [ -n "${!v:-}" ] || miss="$miss $v"; done
if [ -n "$miss" ]; then
  echo "mm: not set in this session:$miss. Add them to the environment's variables, then start a new session." >&2
  exit 2
fi
kh="$HOME/.ssh/known_hosts_mm"; vm="${MM_VM_NAME:-mm-backend}"
if [ ! -s "$kh" ]; then
  # host key comes from Google's guest attributes, so it is verified, not trusted on first use
  gcloud compute instances get-guest-attributes "$vm" --zone="${MM_VM_ZONE:-me-central1-a}" \
    --query-path=hostkeys/ --format='value(key,value)' | awk -v h="$vm" '{print h, $1, $2}' > "$kh.tmp"
  [ -s "$kh.tmp" ] && mv "$kh.tmp" "$kh" || { echo "mm: could not fetch the VM host key" >&2; exit 1; }
fi
exec ssh -F "$HOME/.ssh/mm_config" mm "$@"
EOF
put /usr/local/bin/mmsql <<'EOF'
#!/bin/bash
exec mm "c=\$(sudo docker ps --format '{{.Names}}' | grep -E '[-_]postgres[-_]1\$' | head -1); sudo docker exec -i \"\$c\" sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -v ON_ERROR_STOP=1 ${MMSQL_FLAGS:--x}'" <<< "$*"
EOF
put /usr/local/bin/mmlogs <<'EOF'
#!/bin/bash
svc="${1:?service, e.g. api|pos-api|aggregator-worker|nginx}"; shift
exec mm "c=\$(sudo docker ps --format '{{.Names}}' | grep -E '[-_]${svc}(-(blue|green))?[-_]1\$' | head -1); sudo docker logs ${*:---since 1h} \"\$c\" 2>&1"
EOF
put /usr/local/bin/mm-diag < "$HERE/mm-diag.sh"

seen=""; for v in MM_VM_USER MM_VM_SSH_KEY_B64 MM_GCS_SA_KEY_B64; do [ -n "${!v:-}" ] && seen="$seen $v"; done
step "env vars visible to setup:${seen:- none} (the helpers read them at run time either way)"
step "done: mm, mmsql, mmlogs, mm-diag"
