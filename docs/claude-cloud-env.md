# Claude Code cloud environment for prod debugging

This sets up a [Claude Code cloud environment](https://code.claude.com/docs/en/cloud-environments)
that can reach the prod VM and the GCS buckets, so prod can be debugged from the
Claude mobile app. The scripts live in [`scripts/claude-cloud/`](../scripts/claude-cloud/).

> **This repo is public.** Nothing in git may hold a credential or a private
> identifier. Keys, the VM login user and the service account live only in the
> environment's variables. The scripts read the GCP project and account from the
> key itself, and fetch the VM's SSH host key from Google at runtime.

## How the cloud sandbox behaves

Observed on a Team plan, 2026-10. The [cloud-environments docs](https://code.claude.com/docs/en/cloud-environments)
don't mention most of this.

| | Behaviour | Consequence |
|---|---|---|
| Egress | Traffic goes through a local HTTP CONNECT proxy (`https_proxy=http://127.0.0.1:<port>`), with network access set to Full. Direct TCP to port 22 is blocked, but the proxy does accept `CONNECT host:22`. | SSH uses [IAP TCP forwarding](https://cloud.google.com/iap/docs/using-tcp-forwarding), a WebSocket to `tunnel.cloudproxy.app:443`. It doesn't depend on the proxy's port policy, and the VM never needs SSH open to the internet. |
| Credentials | The sandbox sets `CLOUDSDK_AUTH_ACCESS_TOKEN=proxy-injected` (also `GH_TOKEN` and `AWS_*`). These are placeholders for the "API credentials" feature, which swaps in a real token at the proxy. It **isn't available on Team or Enterprise**. | gcloud prefers that variable over every account, so every call returns `401`. The gcloud wrapper unsets it. |
| Environment variables | Readable by any command in the session (there are no hidden secrets on Team). Environments are personal unless an Owner shares one. | Use dedicated, narrowly scoped keys that are easy to revoke. Never use an Owner-shared environment for this. |
| gcloud | The image ships a gcloud SDK in `/opt/google-cloud-sdk`, symlinked from `/usr/local/bin/gcloud`. It isn't in the docs' tool list, and it works. | `setup.sh` **replaces the symlink** and never writes through it (see the note below). The wrapper runs `<sdk>/lib/gcloud.py` with the SDK's bundled Python. |
| TLS | The sandbox ships its proxy CAs in `/root/.ccr/ca-bundle.crt` and points `CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE`, `SSL_CERT_FILE` and similar variables at it. | Nothing to do. |
| Order and caching | The repo is cloned *before* the setup script runs, and is a fresh clone every session. After the first setup, the environment **snapshots the filesystem and later sessions skip the setup script**. The snapshot is rebuilt only when the setup-script text or the allowed network hosts change, or after about 7 days ([docs](https://code.claude.com/docs/en/cloud-environments)). The environment's variables may not be visible to setup. | Setup installs only tooling and thin stubs in `/usr/local/bin`. Each stub runs `scripts/claude-cloud/bin/<name>` from the session's own clone, and the scripts read the `MM_*` variables when they run. So a fix merged to `main` is live in the next session without re-running setup. To force a full re-setup, bump the `setup-rev` line in the environment's setup script. |

> **Lesson from the first rollout (2026-10-02).** An early `setup.sh` wrote its
> wrapper with `cat > /usr/local/bin/gcloud`. That followed the symlink and
> overwrote the SDK's own launcher with the wrapper, and the wrapper then called
> itself forever. It looked like "the image's gcloud hangs at 0 CPU". A second bug
> wrote the SSH config once, using whatever variables existed at the time, so
> `MM_VM_USER` added later never took effect. Both are fixed: files are written
> with `mv` (which replaces a symlink rather than writing through it), the SSH
> config is regenerated on every call, and setup repairs a launcher the old
> version overwrote.

## What the session can do

- **VM:** a dedicated login user, from an SSH key in the VM's instance metadata (not project metadata), with sudo and docker. That means prod containers, Postgres (`mmsql` is read-write) and logs. It deliberately doesn't use OS Login: turning that on disables metadata keys, which the deploy key relies on.
- **GCS:** a service account with `roles/storage.objectAdmin` on the app buckets and `roles/storage.objectViewer` on the backups bucket, both granted on the bucket. It also has `roles/iap.tunnelResourceAccessor` and `roles/compute.viewer` on the VM only (for IAP and the host key), and no other project roles.

## One-time GCP setup

```bash
P=<project>; Z=<zone>; VM=<vm-name>; U=<vm-login-user>
SA=<sa-name>@$P.iam.gserviceaccount.com

# Service account, scoped per bucket
gcloud iam service-accounts create <sa-name> --project=$P
for b in <app-bucket-1> <app-bucket-2>; do
  gcloud storage buckets add-iam-policy-binding gs://$b --member=serviceAccount:$SA --role=roles/storage.objectAdmin; done
gcloud storage buckets add-iam-policy-binding gs://<backup-bucket> --member=serviceAccount:$SA --role=roles/storage.objectViewer
gcloud storage buckets add-iam-policy-binding gs://<backup-bucket> --member=serviceAccount:$SA --role=roles/storage.legacyBucketReader

# IAP TCP forwarding (SSH over 443): Google's IAP range is the only new source
gcloud services enable iap.googleapis.com --project=$P
gcloud compute firewall-rules create allow-iap-ssh --project=$P --network=default \
  --direction=INGRESS --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20
gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=roles/iap.tunnelResourceAccessor --condition=None
gcloud compute instances add-iam-policy-binding $VM --zone=$Z --project=$P --member=serviceAccount:$SA --role=roles/compute.viewer

# Publish the VM's SSH host keys as guest attributes, so the session can verify the
# host instead of trusting it on first use. The guest agent only does this at first
# boot, and only if guest attributes were already enabled. Run on the VM once:
for f in /etc/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ecdsa_key.pub; do
  curl -sf -X PUT --data "$(cut -d' ' -f2 $f)" -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/guest-attributes/hostkeys/$(cut -d' ' -f1 $f)"
done

# VM login key, added to this instance only. Keep the existing ssh-keys lines.
ssh-keygen -t ed25519 -N "" -C "$U@claude-cloud" -f vm_key
gcloud compute instances describe $VM --zone=$Z --format=json \
  | jq -r '.metadata.items[]|select(.key=="ssh-keys").value' > keys.txt
echo "$U:$(cat vm_key.pub)" >> keys.txt
gcloud compute instances add-metadata $VM --zone=$Z --metadata-from-file ssh-keys=keys.txt

# Service-account key, then the environment variables straight to the clipboard
gcloud iam service-accounts keys create sa.json --iam-account=$SA
printf 'MM_VM_SSH_KEY_B64=%s\nMM_GCS_SA_KEY_B64=%s\nMM_VM_USER=%s\n' \
  "$(base64 < vm_key | tr -d '\n')" "$(base64 < sa.json | tr -d '\n')" "$U" | pbcopy
rm vm_key vm_key.pub sa.json keys.txt
```

## Creating the environment

1. claude.ai/code → cloud icon → **Add cloud environment**. It must be a personal one.
2. **Network access:** Full.
3. **Environment variables:** paste the clipboard (`MM_VM_SSH_KEY_B64`, `MM_GCS_SA_KEY_B64`, `MM_VM_USER`). The optional `MM_VM_NAME` and `MM_VM_ZONE` default to the prod VM.
4. **Setup script:** the contents of [`scripts/claude-cloud/environment-setup-script.sh`](../scripts/claude-cloud/environment-setup-script.sh).
5. Start a **new** session on this repo. Setup prints `[mm-setup] …` lines and ends with `done`.

## Using it

| Command | What it does |
|---|---|
| `mm <cmd>` | Runs a command on the VM over IAP. Use `sudo` for docker. |
| `mmsql "<sql>"` | Runs SQL in the Postgres container as its app user. **Read-write.** Set `MMSQL_FLAGS=-At` for compact output. |
| `mmlogs <svc> [--since 1h]` | Shows container logs (`api`, `pos-api`, `aggregator-worker`, `nginx`, …). |
| `gcloud storage …` | GCS, as the service account. |
| `mm-diag` | (`scripts/claude-cloud/bin/mm-diag`) Checks every layer in about 45 seconds and saves the result to `/tmp/mm-diag.txt`. It prints no secrets, but it does print hostnames and the account. Share it only privately. |

## Troubleshooting

Run `mm-diag` first. Each row below points at a section of its output.

| Symptom | Cause | Fix |
|---|---|---|
| `401 … CLOUDSDK_AUTH_ACCESS_TOKEN` | The image's gcloud was called directly, bypassing the wrapper | Use `/usr/local/bin/gcloud`, or `unset CLOUDSDK_AUTH_ACCESS_TOKEN` |
| gcloud hangs at ~0 CPU | The SDK launcher was overwritten by the wrapper, so it calls itself (§7 shows `clobbered`) | Bump `setup-rev` in the environment's setup script. The re-setup repairs it |
| A fix on `main` doesn't take effect | The session is still on old stubs from a snapshot taken before they existed | Bump `setup-rev` once |
| `Connection timed out during banner exchange` | The IAP ProxyCommand failed (§8–§10) | Check the account is active (§8), the firewall rule exists, and the VM is up |
| `could not fetch the VM host key` | Host keys aren't published, or `compute.viewer` is missing | Re-run the host-key publish step above |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | The VM was rebuilt and its host keys changed | Re-publish the host keys, then `rm ~/.ssh/known_hosts_mm` |
| `mm: not set in this session: …` | Those variables aren't in the environment | Add them, then start a new session |
| `Permission denied (publickey)` | The key was rotated or `MM_VM_USER` is wrong | Re-add the key to instance metadata and update the variable |
| §4 shows `CONNECT tunnel.cloudproxy.app:443 -> 403` | The network level isn't Full | Set Network access to Full |

## Revoking

- **VM:** delete the `<user>:ssh-ed25519 … <user>@claude-cloud` line from the instance's `ssh-keys` metadata.
- **GCS / IAP:** `gcloud iam service-accounts keys delete <key-id> --iam-account=$SA`. Remove everything by deleting the service account and the `allow-iap-ssh` rule.
- After any change, update the environment variables and start a new session.
