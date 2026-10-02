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
# The sandbox only lets HTTPS out through a local proxy, so SSH rides Google's
# IAP TCP tunnel over 443 rather than port 22.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
step() { echo "[mm-setup] $*"; }

step "apt packages"
timeout 120 apt-get update -qq >/dev/null 2>&1 || step "apt update slow/failed, continuing"
timeout 180 apt-get install -y -qq openssh-client postgresql-client jq >/dev/null 2>&1 || step "apt install failed, continuing"

# The image ships a gcloud SDK; reuse it and only download one if it is missing.
step "gcloud"
REAL=""
[ -s /etc/mm-gcloud-real ] && REAL=$(cat /etc/mm-gcloud-real)
if [ -z "$REAL" ] || [ ! -x "$REAL" ]; then
  cur=$(command -v gcloud || true)
  if [ -n "$cur" ] && ! grep -q 'mm-gcloud-wrapper' "$cur" 2>/dev/null; then
    REAL=$(readlink -f "$cur")
    if [ "$REAL" = /usr/local/bin/gcloud ]; then
      mv /usr/local/bin/gcloud /usr/local/bin/gcloud.real; REAL=/usr/local/bin/gcloud.real
    fi
  fi
fi
if [ -z "$REAL" ] || [ ! -x "$REAL" ]; then
  step "downloading gcloud tarball"
  arch=$(uname -m); [ "$arch" = aarch64 ] && arch=arm
  timeout 240 bash -c "curl -fsSL https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-${arch}.tar.gz | tar -xz -C /opt"
  REAL=/opt/google-cloud-sdk/bin/gcloud
fi
echo "$REAL" > /etc/mm-gcloud-real
step "using gcloud SDK at $(dirname "$(dirname "$REAL")")"

step "uv / pnpm"
command -v uv >/dev/null || timeout 90 bash -c 'curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh' >/dev/null 2>&1 || step "uv install failed, continuing"
command -v pnpm >/dev/null || timeout 90 npm install -g pnpm@9 >/dev/null 2>&1 || true

# --- mm-auth: write creds from env vars into $HOME (idempotent, no network) ---
cat > /usr/local/bin/mm-auth <<'EOF'
#!/bin/bash
set -euo pipefail
d="$HOME/.config/mm"; mkdir -p "$HOME/.ssh" "$d"; chmod 700 "$HOME/.ssh" "$d"
if [ -n "${MM_VM_SSH_KEY_B64:-}" ] && [ ! -s "$HOME/.ssh/mm_vm" ]; then
  echo "$MM_VM_SSH_KEY_B64" | base64 -d > "$HOME/.ssh/mm_vm"; chmod 600 "$HOME/.ssh/mm_vm"
fi
if [ -n "${MM_GCS_SA_KEY_B64:-}" ] && [ ! -s "$d/sa.json" ]; then
  echo "$MM_GCS_SA_KEY_B64" | base64 -d > "$d/sa.json"; chmod 600 "$d/sa.json"
fi
if [ -s "$d/sa.json" ] && [ ! -s "$d/env" ]; then
  python3 - "$d/sa.json" > "$d/env" <<'PY'
import json, sys
k = json.load(open(sys.argv[1]))
print(f"MM_GCP_PROJECT={k['project_id']}\nMM_GCP_ACCOUNT={k['client_email']}")
PY
fi
vm="${MM_VM_NAME:-mm-backend}"; zone="${MM_VM_ZONE:-me-central1-a}"
grep -q '^Host mm ' "$HOME/.ssh/config" 2>/dev/null || cat >> "$HOME/.ssh/config" <<CFG
Host mm $vm
  HostName $vm
  HostKeyAlias $vm
  User ${MM_VM_USER:-unset-MM_VM_USER}
  IdentityFile $HOME/.ssh/mm_vm
  IdentitiesOnly yes
  UserKnownHostsFile $HOME/.ssh/known_hosts_mm
  StrictHostKeyChecking yes
  ServerAliveInterval 30
  ConnectTimeout 30
  ProxyCommand /usr/local/bin/gcloud compute start-iap-tunnel $vm 22 --listen-on-stdin --zone=$zone --verbosity=error
CFG
EOF

# --- gcloud wrapper ----------------------------------------------------------
# 1. The sandbox sets CLOUDSDK_AUTH_ACCESS_TOKEN=proxy-injected (a placeholder for
#    the API-credentials feature, which Team plans lack); gcloud prefers it over
#    any account, so every call 401s. Drop it and pin the service account.
# 2. The image's bin/gcloud launcher hangs in the sandbox before Python starts,
#    so run the SDK's entry point with the system python3 instead.
cat > /usr/local/bin/gcloud <<'EOF'
#!/bin/bash
# mm-gcloud-wrapper
unset CLOUDSDK_AUTH_ACCESS_TOKEN CLOUDSDK_AUTH_ACCESS_TOKEN_FILE
mm-auth
d="$HOME/.config/mm"
if [ -s "$d/env" ]; then
  . "$d/env"
  export CLOUDSDK_CORE_ACCOUNT="$MM_GCP_ACCOUNT" CLOUDSDK_CORE_PROJECT="$MM_GCP_PROJECT"
fi
export CLOUDSDK_CORE_CHECK_GCE_METADATA=false CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK=true
export CLOUDSDK_CORE_DISABLE_USAGE_REPORTING=true CLOUDSDK_CORE_DISABLE_PROMPTS=1
SDK=$(cd "$(dirname "$(cat /etc/mm-gcloud-real)")/.." && pwd)
PY=$(command -v python3)
if [ ! -f "$d/.gcloud-activated" ] && [ -s "$d/sa.json" ]; then
  timeout 30 "$PY" "$SDK/lib/gcloud.py" auth activate-service-account --key-file="$d/sa.json" -q >/dev/null 2>&1 \
    && touch "$d/.gcloud-activated"
  # the sandbox proxy re-signs TLS; trust the system store that holds its CA
  [ -f /etc/ssl/certs/ca-certificates.crt ] && \
    timeout 30 "$PY" "$SDK/lib/gcloud.py" config set core/custom_ca_certs_file /etc/ssl/certs/ca-certificates.crt -q >/dev/null 2>&1
fi
exec "$PY" "$SDK/lib/gcloud.py" "$@"
EOF

# --- helpers -----------------------------------------------------------------
# mm <cmd>        run a command on the VM (sudo for docker)
# mmsql "<sql>"   SQL against the prod Postgres container, as its own app user (READ-WRITE)
# mmlogs <svc>    a container's logs, e.g. mmlogs api --since 1h
cat > /usr/local/bin/mm <<'EOF'
#!/bin/bash
set -euo pipefail
mm-auth
kh="$HOME/.ssh/known_hosts_mm"; vm="${MM_VM_NAME:-mm-backend}"
if [ ! -s "$kh" ]; then
  # host key comes from Google's guest attributes, so it is verified, not trusted on first use
  gcloud compute instances get-guest-attributes "$vm" --zone="${MM_VM_ZONE:-me-central1-a}" \
    --query-path=hostkeys/ --format='value(key,value)' | awk -v h="$vm" '{print h, $1, $2}' > "$kh.tmp"
  [ -s "$kh.tmp" ] && mv "$kh.tmp" "$kh" || { echo "mm: could not fetch the VM host key" >&2; exit 1; }
fi
exec ssh -F "$HOME/.ssh/config" mm "$@"
EOF
cat > /usr/local/bin/mmsql <<'EOF'
#!/bin/bash
exec mm "c=\$(sudo docker ps --format '{{.Names}}' | grep -E '[-_]postgres[-_]1\$' | head -1); sudo docker exec -i \"\$c\" sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -v ON_ERROR_STOP=1 ${MMSQL_FLAGS:--x}'" <<< "$*"
EOF
cat > /usr/local/bin/mmlogs <<'EOF'
#!/bin/bash
svc="${1:?service, e.g. api|pos-api|aggregator-worker|nginx}"; shift
exec mm "c=\$(sudo docker ps --format '{{.Names}}' | grep -E '[-_]${svc}(-(blue|green))?[-_]1\$' | head -1); sudo docker logs ${*:---since 1h} \"\$c\" 2>&1"
EOF
install -m 755 "$HERE/mm-diag.sh" /usr/local/bin/mm-diag
chmod 755 /usr/local/bin/mm-auth /usr/local/bin/gcloud /usr/local/bin/mm /usr/local/bin/mmsql /usr/local/bin/mmlogs

mm-auth || step "mm-auth failed: are the MM_* env vars set?"
[ -n "${MM_VM_USER:-}" ] || step "MM_VM_USER is not set: VM helpers will not log in"
step "done: mm, mmsql, mmlogs, mm-diag"
