#!/bin/bash
# mm-diag: one-shot evidence capture for the Claude cloud sandbox -> GCP path.
# Every probe is time-boxed; nothing here prints secret values.
# Output also lands in /tmp/mm-diag.txt. Paste it only into a private chat.
VM="${MM_VM_NAME:-mm-backend}"; ZONE="${MM_VM_ZONE:-me-central1-a}"
exec > >(tee /tmp/mm-diag.txt) 2>&1
T() { local s=$1; shift; timeout "$s" "$@"; local rc=$?; [ $rc -eq 124 ] && echo "  !! TIMEOUT after ${s}s"; return $rc; }
H() { echo; echo "===== $* ====="; }
PX="${HTTPS_PROXY:-${https_proxy:-}}"

H "1. sandbox"
uname -a; grep PRETTY /etc/os-release; id; echo "HOME=$HOME passwd_home=$(getent passwd "$(id -un)" | cut -d: -f6)"
(dmesg 2>/dev/null | head -3) || true
[ -e /proc/self/status ] && grep -E '^(Seccomp|NoNewPrivs)' /proc/self/status
nproc; head -1 /proc/meminfo

H "2. proxy / credential env (values masked unless placeholder)"
env | grep -iE 'proxy|cloudsdk|token|_key|google|gcloud|ccr_|ca_|ssl|cert' | grep -v '^MM_' | sort | \
  awk -F= '{v=substr($0,length($1)+2); if ($1 ~ /TOKEN|KEY|SECRET/ && v != "proxy-injected") v="<set, " length(v) " chars>"; print $1"="v}'
echo "MM_VM_SSH_KEY_B64: ${MM_VM_SSH_KEY_B64:+set (${#MM_VM_SSH_KEY_B64} chars)}"
echo "MM_GCS_SA_KEY_B64: ${MM_GCS_SA_KEY_B64:+set (${#MM_GCS_SA_KEY_B64} chars)}"
echo "MM_VM_USER: ${MM_VM_USER:+set}${MM_VM_USER:-NOT SET}"; echo "MM_VM_NAME=${MM_VM_NAME:-<default>} MM_VM_ZONE=${MM_VM_ZONE:-<default>}"

H "3. TLS interception: who signs storage.googleapis.com?"
T 15 curl -sv -o /dev/null https://storage.googleapis.com/ 2>&1 | grep -E 'issuer:|subject:|SSL certificate verify|HTTP/' | head -5
ls /usr/local/share/ca-certificates/ 2>/dev/null; echo "SSL_CERT_FILE=${SSL_CERT_FILE:-} REQUESTS_CA_BUNDLE=${REQUESTS_CA_BUNDLE:-} NODE_EXTRA_CA_CERTS=${NODE_EXTRA_CA_CERTS:-}"

H "4. egress: direct vs via proxy CONNECT"
for hp in github.com:22 1.1.1.1:443 storage.googleapis.com:443; do
  h=${hp%:*}; p=${hp#*:}
  T 6 bash -c "</dev/tcp/$h/$p" 2>/dev/null && echo "direct  $hp OPEN" || echo "direct  $hp blocked"
done
# Ask the proxy for a CONNECT tunnel and print its status line (200 = allowed).
T 40 python3 - "$PX" <<'PY'
import socket, sys
u = sys.argv[1].split('://')[-1].rstrip('/'); h, p = u.rsplit(':', 1)
for target in ["storage.googleapis.com:443", "oauth2.googleapis.com:443",
               "tunnel.cloudproxy.app:443", "github.com:22"]:
    try:
        s = socket.create_connection((h, int(p)), timeout=8)
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        line = s.recv(200).split(b"\r\n")[0].decode(errors="replace")
        print(f"CONNECT {target:30} -> {line or 'no reply'}")
    except Exception as e:
        print(f"CONNECT {target:30} -> {type(e).__name__}: {e}")
PY

H "5. GCE metadata server (gcloud probes this unless told not to)"
T 6 curl -s -o /dev/null -w 'via proxy: %{http_code}\n' http://169.254.169.254/ || true
T 6 curl -s -o /dev/null --noproxy '*' -w 'direct: %{http_code}\n' http://169.254.169.254/ || true

H "6. websocket upgrade through the proxy (IAP uses wss://tunnel.cloudproxy.app)"
T 15 curl -s -o /dev/null -w 'tunnel.cloudproxy.app upgrade -> HTTP %{http_code}\n' --http1.1 \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' https://tunnel.cloudproxy.app/v4/connect

H "7. gcloud SDK layout (the wrapper must not have overwritten the SDK launcher)"
cat /etc/mm-gcloud 2>/dev/null || echo "no /etc/mm-gcloud: setup did not finish"
. /etc/mm-gcloud 2>/dev/null || true
which -a gcloud gsutil 2>/dev/null | while read -r p; do echo "$p -> $(readlink -f "$p")"; done
if [ -n "${SDK:-}" ]; then
  grep -q 'mm-gcloud-wrapper' "$SDK/bin/gcloud" && echo "!! $SDK/bin/gcloud is the wrapper (clobbered)" || echo "SDK launcher intact: $(head -c 60 "$SDK/bin/gcloud" | head -1)"
  echo "-- SDK python:"; T 15 "$PY" -c 'import sys,ssl;print("ok",sys.version.split()[0],ssl.OPENSSL_VERSION)'
  echo "-- SDK's own launcher (should exit, not hang):"; T 30 env -u CLOUDSDK_AUTH_ACCESS_TOKEN "$SDK/bin/gcloud" --version 2>&1 | head -1
fi

H "8. our wrapper (/usr/local/bin/gcloud)"
time (T 40 gcloud --version 2>&1 | head -1)
T 20 gcloud auth list 2>&1 | head -4
T 20 gcloud config list 2>&1 | grep -vE 'token' | head -10

H "9. GCS through the wrapper"
# The service account is granted per bucket, so listing the project's buckets is
# expected to 403. Set MM_DIAG_BUCKET to probe one bucket it should read.
if [ -n "${MM_DIAG_BUCKET:-}" ]; then T 60 gcloud storage ls "gs://$MM_DIAG_BUCKET/" 2>&1 | head -3
else echo "(set MM_DIAG_BUCKET=<bucket> to probe a bucket)"; T 30 gcloud storage buckets list --format='value(name)' 2>&1 | head -2 | cut -c1-90; fi

H "10. IAP tunnel: does the VM's SSH banner come back over wss?"
T 40 bash -c "sleep 8 | gcloud compute start-iap-tunnel $VM 22 --listen-on-stdin --zone=$ZONE --verbosity=error 2>/tmp/iap.err | head -c 60; echo" ; tail -5 /tmp/iap.err 2>/dev/null

H "11. end to end"
T 90 mm hostname 2>&1 | tail -3

echo; echo "saved to /tmp/mm-diag.txt"
