# Production Deployment Guide — Melting Moments Ecommerce

## Architecture

```
Internet
├── meltingmomentscakes.com        → Vercel (web storefront)
├── admin.meltingmomentscakes.com  → Vercel (admin panel)
├── api.meltingmomentscakes.com    → GCP VM: FastAPI via Nginx + SSL
├── pos.meltingmomentscakes.com    → GCP VM: the register API, its own app + container
└── pub-<hash>.r2.dev              → Cloudflare R2 (object storage)
```

**GCP VM** (e2-micro, 1 vCPU shared, 1 GB RAM) runs:
- PostgreSQL 16
- Redis 7 (response caching)
- FastAPI (Uvicorn) — the storefront/admin API
- FastAPI (Uvicorn) — the register API (`app.pos_main`), a narrower route table
- Nginx (reverse proxy for `api.*` and `pos.*`)
- Certbot (SSL for both)
- 2 GB swapfile — an e2-micro has no headroom for a second app process

**Vercel** hosts both Next.js apps — free Hobby plan, global CDN, automatic deployments on push to `main`.

---

## Monthly Cost Estimate

| Service | Provider | Plan | Est. $/mo |
|---------|----------|------|-----------|
| Web storefront (Next.js) | Vercel | Hobby (free) | $0 |
| Admin panel (Next.js) | Vercel | Hobby (free, same account) | $0 |
| Backend VM (e2-micro) | GCP Compute Engine | On-demand + sustained discount | ~$4 |
| Boot disk (20 GB SSD) | GCP Persistent Disk | Standard SSD | ~$5 |
| Database backups | GCP Cloud Storage | Standard, ~2 GB, 90-day lifecycle | ~$0.05 |
| Boot-disk snapshots | GCP Compute | Daily, 14-day retention, incremental | ~$0.50 |
| Network egress | GCP | ~2.3 GB/mo (measured 2026-08-21) | ~$0.20 |
| Media storage | Cloudflare R2 | Free tier (10 GB) | $0 |
| SSL certificates | Let's Encrypt | Free | $0 |
| Analytics | Umami Cloud | Free tier | $0 |
| **Total** | | | **~$10–11/mo** |

---

## Prerequisites

- [ ] GCP account with billing enabled
- [ ] Domain registered and pointing to Cloudflare (for DNS management)
- [ ] Cloudflare R2 bucket created (`melting-moments-cakes`, public access enabled)
- [ ] Stripe account with live API keys + webhook configured
- [ ] Resend account with verified sending domain
- [ ] Vercel account (free Hobby plan is sufficient)
- [ ] GitHub repository with this codebase
- [ ] Local `gcloud` CLI installed (for initial setup)

---

## Step 1: GCP Project + Billing

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a new project: `melting-moments-cakes`
2. Enable billing for the project
3. Enable the Compute Engine API:
   ```
   gcloud services enable compute.googleapis.com --project=melting-moments-cakes
   ```

---

## Step 2: Create Compute Engine VM

```bash
gcloud compute instances create mm-backend \
  --project=melting-moments-cakes \
  --zone=me-central1-a \
  --machine-type=e2-micro \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=20GB \
  --boot-disk-type=pd-ssd \
  --tags=http-server,https-server \
  --scopes=cloud-platform
```

> **Zone choice:** `me-central1-a` (Doha, Qatar) is closest to the UAE. Alternatively use `europe-west1` for slightly lower cost if latency is acceptable.

Note the **External IP** assigned — you'll need it for DNS.

---

## Step 3: Configure GCP Firewall Rules

```bash
# Allow HTTP (for Certbot challenge) and HTTPS
gcloud compute firewall-rules create allow-http-https \
  --project=melting-moments-cakes \
  --allow=tcp:80,tcp:443 \
  --target-tags=http-server,https-server \
  --description="Allow web traffic"

# SSH is already open by default (tcp:22)
```

---

## Step 4: SSH into VM, Install Dependencies

```bash
# SSH via gcloud (handles key management automatically)
gcloud compute ssh mm-backend --project=melting-moments-cakes --zone=me-central1-a
```

Inside the VM:

```bash
# Update packages
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

# Install Docker Compose plugin (included with Docker >=24)
docker compose version  # verify

# Install gcloud CLI (for gsutil)
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
gcloud init  # follow prompts; select project melting-moments-cakes
```

---

## Step 5: Clone Repo + Configure Environment

```bash
sudo mkdir -p /opt/melting-moments-cakes
sudo chown $USER:$USER /opt/melting-moments-cakes

git clone https://github.com/your-org/melting-moments-cakes.git /opt/melting-moments-cakes
cd /opt/melting-moments-cakes
```

> **No manual `.env` editing needed.** The `deploy.yml` workflow writes `/opt/melting-moments-cakes/.env` automatically on every deploy, sourcing all values from GitHub Actions secrets (see Step 13c). Before your first deploy, add all secrets to GitHub first.

---

## Step 6: Launch Backend Services

```bash
cd /opt/melting-moments-cakes

# Pull base images + build API
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml build api

# Start all backend services
docker compose -f docker-compose.prod.yml up -d

# Verify all containers are running
docker compose -f docker-compose.prod.yml ps

# Run migrations + seed admin user
# If you get "Multiple head revisions" error, run the two commands below first:
#   docker compose -f docker-compose.prod.yml exec api alembic upgrade heads
#   docker compose -f docker-compose.prod.yml exec api alembic merge heads -m "merge_heads"
docker compose -f docker-compose.prod.yml exec api alembic upgrade head
docker compose -f docker-compose.prod.yml exec api python -m scripts.seed_db
```

Expected running services: `redis`, `postgres`, `api`, `nginx`, `certbot`

---

## Step 7: Issue SSL Certificates

Wait until DNS is pointing `api.*` to the VM IP (Step 12 first).

```bash
cd /opt/melting-moments-cakes

# Issue cert for api subdomain
docker compose -f docker-compose.prod.yml run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d api.meltingmomentscakes.com

# Reload nginx to pick up certs
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
```

Verify: `curl https://api.meltingmomentscakes.com/health` should return `{"status": "ok"}`.

---

## Step 8: GCP Cloud Storage Bucket (offsite backups)

> **History.** Everything in this step was documented from the start and *none of
> it was ever run*. The 2026-08-21 audit found no bucket in the project, no
> `mm-backup-sa`, and therefore not one offsite copy of the database in the five
> months the shop had been live — while `BACKUP_GCS_BUCKET` sat in `.env` looking
> like it was working. The commands below are the ones that were actually run.

```bash
gcloud storage buckets create gs://melting-moments-cakes-backups \
  --project=melting-moments-cakes \
  --location=me-central1 \
  --default-storage-class=STANDARD \
  --uniform-bucket-level-access \
  --public-access-prevention

# Local retention is 7 days; offsite keeps 90, then ages out on its own so the
# bucket cannot quietly become a bill.
cat > lifecycle.json <<'EOF'
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 90}}]}
EOF
gcloud storage buckets update gs://melting-moments-cakes-backups \
  --lifecycle-file=lifecycle.json
```

**No dedicated service account.** This step used to create `mm-backup-sa` and
re-attach it to the VM — which requires stopping the instance. That was not done,
and it is not necessary: the VM runs as the project's default compute service
account (`136865397988-compute@developer.gserviceaccount.com`), which holds
`roles/editor` and can therefore already write to the bucket. The upload was
verified end-to-end after the bucket existed.

Worth doing later, but deliberately *not* done during the audit because it needs
an instance stop: give the VM a dedicated account with `roles/storage.objectCreator`
on this bucket only. `objectCreator` can add backups but not delete them, so a
compromise of the VM cannot erase the offsite copies — which `roles/editor`
currently can.

`BACKUP_GCS_BUCKET=melting-moments-cakes-backups` is already set in `.env`.

---

## Step 8b: Boot-Disk Snapshot Schedule

The database dump protects against a bad migration. It does not protect against
losing the VM, and until 2026-08-21 nothing did — the project had zero snapshots
and no schedule, so the dumps and the volume they came from shared one disk.

```bash
gcloud compute resource-policies create snapshot-schedule mm-backend-daily-snapshot \
  --project=melting-moments-cakes \
  --region=me-central1 \
  --max-retention-days=14 \
  --on-source-disk-delete=keep-auto-snapshots \
  --daily-schedule \
  --start-time=03:00 \
  --storage-location=me-central1

gcloud compute disks add-resource-policies mm-backend \
  --project=melting-moments-cakes \
  --zone=me-central1-a \
  --resource-policies=mm-backend-daily-snapshot
```

03:00 UTC is deliberate: it is an hour after the backup cron in Step 9, so every
snapshot contains a dump taken that morning.

---

## Step 9: Schedule Automated Backups (Cron)

> **History.** This too was documented and never installed. `crontab -l` was empty
> for both users until 2026-08-21. Backups happened only because `deploy.yml` takes
> one before migrating, which hid the gap behind a busy deploy cadence — a quiet
> week was a week with no backup at all.

The crontab belongs to **`hussainabbasi786110`**, the user the deploy runs as and
the owner of `backups/`. `crontab -e` as whoever you happen to be logged in as is
how this gets installed into the wrong account and silently never runs:

```bash
sudo crontab -u hussainabbasi786110 -e
```

```
0 2 * * * DEPLOY_DIR=/opt/melting-moments-cakes /opt/melting-moments-cakes/scripts/backup-db.sh >> /var/log/mm-backup.log 2>&1
```

`/var/log/mm-backup.log` must exist and be owned by that user, and is rotated
weekly by `/etc/logrotate.d/mm-backup` (8 weeks, compressed).

**Test it the way cron will run it, not the way your shell will.** The difference
is the whole reason the GCS upload never worked: `gcloud` lives in a home
directory and is put on `PATH` by `.bashrc`, which non-interactive shells do not
source. `backup-db.sh` now finds the SDK itself, and this is the check that proves
it:

```bash
sudo -u hussainabbasi786110 env -i \
  HOME=/home/hussainabbasi786110 PATH=/usr/bin:/bin SHELL=/bin/sh \
  DEPLOY_DIR=/opt/melting-moments-cakes \
  /bin/sh -c /opt/melting-moments-cakes/scripts/backup-db.sh

# The only proof that counts:
gcloud storage ls -l gs://melting-moments-cakes-backups/backups/
```

A backup that has never been restored is a hypothesis. To test one:

```bash
gcloud storage cp gs://melting-moments-cakes-backups/backups/<file>.sql.gz /tmp/t.sql.gz
docker exec melting-moments-cakes-postgres-1 psql -U mm_user -d postgres -c "CREATE DATABASE restore_test;"
gunzip -c /tmp/t.sql.gz | docker exec -i melting-moments-cakes-postgres-1 psql -U mm_user -d restore_test -q
# compare row counts against the live database, then:
docker exec melting-moments-cakes-postgres-1 psql -U mm_user -d postgres -c "DROP DATABASE restore_test;"
```

---

## Step 10: Vercel — Web Storefront

1. Go to [vercel.com](https://vercel.com) and sign in with GitHub
2. Click **Add New Project** → import `melting-moments-cakes`
3. Configure:
   - **Framework Preset**: Next.js
   - **Root Directory**: `apps/web`
   - **Build Command**: `cd ../.. && pnpm build --filter=web`
   - **Output Directory**: `.next`
4. Add **Environment Variables**:
   ```
   NEXT_PUBLIC_SITE_URL=https://meltingmomentscakes.com
   NEXT_PUBLIC_API_URL=https://api.meltingmomentscakes.com/api/v1
   NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_live_CHANGE_ME
   NEXT_PUBLIC_SUPPORTED_LOCALES=en,ar
   NEXT_PUBLIC_UMAMI_WEBSITE_ID=<from Umami Cloud dashboard>
   NEXT_PUBLIC_CLARITY_PROJECT_ID=<from clarity.microsoft.com → Settings → Overview>
   ```
   > `NEXT_PUBLIC_CLARITY_PROJECT_ID` turns on Microsoft Clarity — session
   > recordings and heatmaps. Public by design, like the Umami website ID: it
   > identifies the project and authorises nothing, and there is no secret half
   > to set on the API or in GitHub Actions. Leave it empty and the storefront
   > renders no script, makes no request and sets no cookie. Setup and the
   > dashboard configuration it expects — masking above all — are in
   > `docs/microsoft-clarity-setup.md`; read the Privacy section before turning
   > it on, because unlike Umami this one records the page.
   > Do **not** add `NEXT_PUBLIC_UMAMI_URL`. The analytics paths are internal to
   > the storefront and hard-coded in `app/layout.tsx`; a stale value here once
   > pointed the tracker at a 404 and stopped analytics dead. If the project
   > still has one set, remove it.
   > Sentry env vars (`NEXT_PUBLIC_SENTRY_DSN`, `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT`) are added separately — see **Step 11c**.
5. Click **Deploy** and note the preview URL (e.g. `melting-moments-cakes-web.vercel.app`)
6. Once confirmed working, go to **Settings → Domains** → add `meltingmomentscakes.com`

---

## Step 11: Vercel — Admin Panel

1. In the same Vercel account, click **Add New Project** → import `melting-moments-cakes` again
2. Configure:
   - **Framework Preset**: Next.js
   - **Root Directory**: `apps/admin`
   - **Build Command**: `cd ../.. && pnpm build --filter=admin`
   - **Output Directory**: `.next`
3. Add **Environment Variables**:
   ```
   NEXT_PUBLIC_API_URL=/api/v1
   NEXT_PRIVATE_API_HOST=https://api.meltingmomentscakes.com
   ```
   > **Why `/api/v1` and not the full URL?** The admin app is deployed on `vercel.app` (a different eTLD+1 from `api.meltingmomentscakes.com`). Browsers block `SameSite=Lax` cookies on cross-site JS fetch calls, so every API call after login would return 401. Using a relative path routes requests through the built-in Next.js proxy (`next.config.ts`), keeping cookies same-origin. Once you add the custom domain `admin.meltingmomentscakes.com` (same site as the API), you may switch to the absolute URL if desired.
4. Click **Deploy** and verify the preview URL
5. Go to **Settings → Domains** → add `admin.meltingmomentscakes.com`

---

## Step 11b: Cloudflare R2 (Media Storage)

R2 stores product images and other uploaded assets.

### Create the bucket

1. Log in to [dash.cloudflare.com](https://dash.cloudflare.com) and select your account
2. In the left sidebar, go to **R2 Object Storage**
3. Click **Create bucket**
   - **Name**: `melting-moments-cakes`
   - **Location**: leave as automatic (Cloudflare picks the closest region to the first request)
4. Click **Create bucket**

### Enable public access

1. Open the bucket → **Settings** tab
2. Under **Public access** → **R2.dev subdomain**, click **Allow Access**
3. Confirm — Cloudflare shows a permanent URL like `https://pub-<hash>.r2.dev`
4. Copy that URL — it goes into `CLOUDFLARE_R2_PUBLIC_URL` in Step 13c

### Create an R2 API token

The API needs write access to upload media files.

1. From the R2 overview page, find the **Account Details** panel on the right side
2. Click **Manage** next to **API Tokens**
3. Choose **Create API Token** (Account API token — not User API token)
4. Configure the token:
   - **Token name**: `melting-moments-api`
   - **Permissions**: `Object Read & Write`
   - **Bucket**: select `melting-moments-cakes` (scope to this bucket only)
5. Click **Create API Token**
6. **Copy immediately** — the Secret Access Key is shown only once:
   - **Access Key ID** → `CLOUDFLARE_R2_ACCESS_KEY`
   - **Secret Access Key** → `CLOUDFLARE_R2_SECRET_KEY`

### Find your endpoint and account ID

Your **Account ID** is visible in the **Account Details** panel on the R2 overview page (also in the dashboard URL: `dash.cloudflare.com/<account-id>/r2`).

The S3-compatible endpoint is:
```
https://<account-id>.r2.cloudflarestorage.com
```
→ `CLOUDFLARE_R2_ENDPOINT`

### Summary of values for Step 13c

| Secret | Where to find it |
|--------|-----------------|
| `CLOUDFLARE_R2_ACCESS_KEY` | API token creation page (Access Key ID) |
| `CLOUDFLARE_R2_SECRET_KEY` | API token creation page (Secret Access Key) |
| `CLOUDFLARE_R2_BUCKET` | `melting-moments-cakes` (literal) |
| `CLOUDFLARE_R2_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` |
| `CLOUDFLARE_R2_PUBLIC_URL` | `https://pub-<hash>.r2.dev` (from bucket Settings → R2.dev subdomain) |

---

## Step 11c: Sentry Error Monitoring

Sentry captures unhandled exceptions, React render errors, App Router request errors, source maps, and FastAPI 500s. Use separate Sentry projects so storefront, admin, and API issues stay separated.

### Create Sentry projects

Create these projects in the same Sentry org:

| Runtime | Sentry project slug | Platform |
|---|---|---|
| Storefront frontend | `mm-frontend` | Next.js |
| Admin frontend | `mm-admin` | Next.js |
| Ecommerce API | `mm-backend` | Python / FastAPI |

For each project, copy the DSN from **Project Settings → Client Keys (DSN)**.

### Create a Sentry auth token

This token is only for source-map upload during Vercel builds.

1. Go to Sentry → **Settings → Auth Tokens → Create New Token**
2. Grant `project:releases` and `org:read`
3. Add the token as `SENTRY_AUTH_TOKEN` in each Vercel project

### Vercel env — storefront

Add these in Vercel → storefront project → **Settings → Environment Variables**:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_SENTRY_DSN` | DSN from `mm-frontend` |
| `NEXT_PUBLIC_APP_ENV` | `production` |
| `NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE` | `0.02` |
| `NEXT_PUBLIC_SENTRY_REPLAYS_SESSION_SAMPLE_RATE` | `0` |
| `SENTRY_AUTH_TOKEN` | Sentry auth token |
| `SENTRY_ORG` | Sentry org slug |
| `SENTRY_PROJECT` | `mm-frontend` |

### Vercel env — admin

Add these in Vercel → admin project:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_SENTRY_DSN` | DSN from `mm-admin` |
| `NEXT_PUBLIC_APP_ENV` | `production` |
| `NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE` | `0.02` |
| `NEXT_PUBLIC_SENTRY_REPLAYS_SESSION_SAMPLE_RATE` | `0` |
| `SENTRY_AUTH_TOKEN` | Sentry auth token |
| `SENTRY_ORG` | Sentry org slug |
| `SENTRY_PROJECT` | `mm-admin` |

### GitHub secret — ecommerce API

Add this to GitHub → `hussu97/mm-ecommerce` → **Settings → Environments → production → Secrets**:

| Secret | Value |
|---|---|
| `SENTRY_DSN` | DSN from `mm-backend` |

The deploy workflow writes `SENTRY_DSN` and `SENTRY_ENVIRONMENT=production` into `/opt/melting-moments-cakes/.env`, and `docker-compose.prod.yml` passes them into the API container.
The API defaults to `SENTRY_TRACES_SAMPLE_RATE=0.02` in production; add that secret only if you want to override the free-tier-friendly default.

### How it works

**Next.js apps**
- `instrumentation-client.ts` initializes browser Sentry and exports `onRouterTransitionStart`.
- `instrumentation.ts` registers Node/Edge Sentry and exports `onRequestError`.
- `app/error.tsx` and `app/global-error.tsx` capture React render errors.
- `next.config.ts` wraps with `withSentryConfig` only when Sentry env exists; source-map upload is enabled only when `SENTRY_AUTH_TOKEN` is present.

**FastAPI API**
- `sentry_sdk.init()` runs before app creation in `apps/api/app/main.py`.
- The custom 500 handler explicitly calls `sentry_sdk.capture_exception()` so JSON 500 responses are still reported.
- PII is disabled with `send_default_pii=False`.

### Verification

After deploying, trigger an authenticated backend smoke test:

```bash
curl -X POST https://api.meltingmomentscakes.com/api/v1/sentry-debug \
  -H "Authorization: Bearer <admin-access-token>"
```

Expected: HTTP 500, followed by a new issue in Sentry project `mm-backend`.

Check **Sentry → Issues** for your organisation — the event should appear within a few seconds.

---

## Step 12: DNS Configuration

Add these records at **Namecheap → Domain List → Manage → Advanced DNS**:

| Type | Name | Value | Notes |
|------|------|-------|-------|
| A | `@` | GCP VM external IP | Web storefront root (apex) |
| CNAME | `www` | `cname.vercel-dns.com` | Vercel redirect |
| CNAME | `admin` | `cname.vercel-dns.com` | Admin panel |
| A | `api` | GCP VM external IP | FastAPI backend |

> **Vercel custom domains**: After adding a custom domain in Vercel, it will show you the exact DNS record needed (either A or CNAME depending on apex vs subdomain). Follow those instructions; the values above are typical.

> **Namecheap tip**: CNAME records for the apex domain (`@`) are not supported — use the A record for `@` pointing to the VM IP instead. For `www`, Namecheap supports CNAME fine.

---

## Step 13: SSH Key Setup + GitHub Actions Secrets

### 13a: Generate a dedicated deploy key

Run this **locally** (not on the VM). Do not use your personal SSH key.

```bash
ssh-keygen -t ed25519 -C "mm-deploy-key" -f ~/.ssh/mm_deploy_key -N ""
```

This creates two files:
- `~/.ssh/mm_deploy_key` — **private key** (goes into GitHub secrets)
- `~/.ssh/mm_deploy_key.pub` — **public key** (goes into GCP metadata)

### 13b: Add the public key to GCP VM metadata

> **Important:** Do NOT manually edit `~/.ssh/authorized_keys` on the VM. GCP's guest agent periodically overwrites that file from instance metadata, removing any keys you added by hand. The only persistent way to add SSH keys is via GCP metadata.

```bash
# Get your VM username (the short name before the @ in your gcloud SSH prompt)
gcloud compute ssh mm-backend --project=melting-moments-cakes --zone=me-central1-a --command="whoami"
# e.g. output: hussain

# Add the public key to the instance metadata (replace USERNAME with the output above)
gcloud compute instances add-metadata mm-backend \
  --project=melting-moments-cakes \
  --zone=me-central1-a \
  --metadata ssh-keys="USERNAME:$(cat ~/.ssh/mm_deploy_key.pub)"
```

Verify it works:
```bash
ssh -i ~/.ssh/mm_deploy_key USERNAME@$(gcloud compute instances describe mm-backend \
  --project=melting-moments-cakes --zone=me-central1-a \
  --format="get(networkInterfaces[0].accessConfigs[0].natIP)")
```

### 13c: Add secrets to GitHub Actions

In GitHub → repo → **Settings → Secrets and variables → Actions → Environments → production**, add every secret in the table below.

The `deploy.yml` workflow SSHes into the GCP VM on every push to `main`, writes the `.env` file from these secrets, runs migrations, and restarts the API. Vercel handles web + admin deployments automatically via its GitHub integration.

#### SSH connection

| Secret | Value | How to get it |
|--------|-------|---------------|
| `SERVER_HOST` | GCP VM external IP | `gcloud compute instances describe mm-backend --project=melting-moments-cakes --zone=me-central1-a --format="get(networkInterfaces[0].accessConfigs[0].natIP)"` |
| `SERVER_USER` | VM username | `gcloud compute ssh mm-backend --zone=me-central1-a --command="whoami"` |
| `SERVER_SSH_KEY` | Private key contents | `cat ~/.ssh/mm_deploy_key` |

#### App

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `APP_ENV` | `production` | Literal |
| `USE_SSL` | `true` | Set to `false` until SSL certs are issued (Step 7) |

#### Database

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `POSTGRES_USER` | e.g. `mm_user` | Choose your own — must match `DATABASE_URL` |
| `POSTGRES_PASSWORD` | strong password | Choose your own — must match `DATABASE_URL` |
| `POSTGRES_DB` | `mm_ecommerce` | Choose your own — must match `DATABASE_URL` |
| `DATABASE_URL` | `postgresql+asyncpg://<user>:<password>@postgres:5432/<db>` | Use `postgres` (container hostname), not `localhost` |
| `REDIS_URL` | `redis://redis:6379/0` | Use `redis` (container hostname), not `localhost` |

#### Security

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `SECRET_KEY` | 64-char hex string | `openssl rand -hex 32` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Literal |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Literal |
| `PASSWORD_RESET_EXPIRE_MINUTES` | `60` | Literal |

#### CORS & allowed hosts

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `CORS_ORIGINS` | `["https://meltingmomentscakes.com","https://admin.meltingmomentscakes.com"]` | JSON array, no spaces |
| `ALLOWED_HOSTS` | `["api.meltingmomentscakes.com"]` | JSON array, no spaces |
| `POS_CORS_ORIGINS` | *(leave empty)* | Only needed for a browser-based terminal — a native iPad sends no Origin |

`POS_ALLOWED_HOSTS` is **not** a secret and is written directly by the deploy
workflow. It is kept apart from `ALLOWED_HOSTS` so the storefront's host list
and the register's cannot drift into one another.

#### pos.meltingmomentscakes.com

**Live since 26 July 2026.** The register runs as its own application
(`app.pos_main`) in its own container, carrying only what a till needs, and
`POS_REQUIRE_POS_HOST=true` means the storefront API refuses device tokens
outright. The steps below are kept for rebuilding the host from scratch.

1. **DNS** — add an `A` record for `pos.meltingmomentscakes.com` pointing at the
   VM's external IP, at the same registrar that serves the other records.
2. **Certificate** — once DNS resolves, issue one:
   ```
   docker compose -f docker-compose.prod.yml run --rm --entrypoint sh certbot -c \
     "certbot certonly --webroot -w /var/www/certbot -d pos.meltingmomentscakes.com \
      --non-interactive --agree-tos -m orders@meltingmomentscakes.com"
   ```
   nginx notices the new certificate by itself within five minutes and starts
   serving the host — no deploy needed. Its server block is deliberately absent
   until the certificate exists, because nginx refuses to start when a
   configured certificate file is missing and that would take the storefront
   API down with it.
3. **Cut the terminals over** — point each iPad at
   `https://pos.meltingmomentscakes.com/api/v1`, then set
   `POS_REQUIRE_POS_HOST=true` so the storefront API stops accepting device
   tokens altogether. Until that flag is set the old host still works and logs
   a warning on every such request, so you can watch the log go quiet before
   flipping it — which is how this cutover was done.

#### Stripe

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `STRIPE_SECRET_KEY` | `sk_live_...` | Stripe dashboard → Developers → API keys |
| `STRIPE_PUBLISHABLE_KEY` | `pk_live_...` | Stripe dashboard → Developers → API keys |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` | Stripe dashboard → Developers → Webhooks → signing secret |

#### Ziina (second card gateway — **not live**)

Ziina is built, wired and switched off. It exists so that a Stripe incident can
be answered by flipping a row in **Admin → Payment Gateways** instead of cutting
a release. Leaving every secret below unset is the supported production state:
the gateway row ships inactive, `ZIINA_ENABLED` defaults false in three separate
files, and the admin refuses to activate a gateway that has no credentials — so
the toggle is visible on production and cannot do anything.

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `ZIINA_ENABLED` | `false` | The master switch. Leave false until Ziina is signed off. |
| `ZIINA_API_KEY` | *(unset)* | Ziina dashboard → Developers → API keys |
| `ZIINA_WEBHOOK_SECRET` | *(unset)* | Your own value — set it and the URL together via `POST /webhook` |
| `ZIINA_API_URL` | *(unset)* | Defaults to `https://api-v2.ziina.com/api` |
| `ZIINA_TEST_MODE` | *(unset)* | `true` sends `test: true` on every intent — sandbox only |
| `ZIINA_TIMEOUT_SECONDS` | *(unset)* | Defaults to `10` |

**To go live on Ziina later**, in this order: set `ZIINA_API_KEY` and
`ZIINA_WEBHOOK_SECRET`, set `ZIINA_ENABLED=true`, deploy (the API refuses to
boot if the flag is on and either secret is missing), register the webhook at
`https://api.meltingmomentscakes.com/api/v1/webhooks/ziina`, then activate the
row in the admin. To make Ziina the *primary*, give it a lower priority number
than Stripe; to keep it purely as a standby, leave the priorities alone and it
will only be reached when Stripe cannot produce a session.

#### Email (Resend)

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `RESEND_API_KEY` | `re_...` | Resend dashboard → API Keys |
| `FROM_EMAIL` | `noreply@meltingmomentscakes.com` | Must match a verified Resend sending domain. Unmonitored — the emails tell customers not to reply. |

#### Cloudflare R2 (media storage)

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `CLOUDFLARE_R2_ACCESS_KEY` | R2 token access key | Cloudflare dashboard → R2 → Manage R2 API Tokens |
| `CLOUDFLARE_R2_SECRET_KEY` | R2 token secret key | Same page as above |
| `CLOUDFLARE_R2_BUCKET` | `melting-moments-cakes` | Literal |
| `CLOUDFLARE_R2_ENDPOINT` | `https://<account_id>.r2.cloudflarestorage.com` | Cloudflare dashboard → R2 → bucket → Settings |
| `CLOUDFLARE_R2_PUBLIC_URL` | `https://pub-<hash>.r2.dev` | From bucket Settings → R2.dev subdomain |

#### BNPL — Tabby

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `TABBY_API_KEY` | `sk_...` | Tabby merchant dashboard |
| `TABBY_PUBLIC_KEY` | `pk_...` | Tabby merchant dashboard |
| `TABBY_MERCHANT_CODE` | your code | Tabby merchant dashboard |

#### BNPL — Tamara

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `TAMARA_API_KEY` | API key | Tamara merchant dashboard |
| `TAMARA_API_URL` | `https://api.tamara.co` | Literal (use `https://api-sandbox.tamara.co` for staging) |

#### Courier — Lalamove

Optional. Leave the key and secret unset and zones marked `lalamove` behave
exactly like third-party ones — same price, dispatched by hand.

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `LALAMOVE_API_KEY` | `pk_prod_...` | Partner Portal → Developers, **Production** tab |
| `LALAMOVE_API_SECRET` | `sk_prod_...` | Same screen. Also signs inbound webhooks |

Which kitchen a run collects from, and the number the driver calls, are **not
settings** — they are columns on the branch (Admin → Branches). A zone names its
own branch; anything without a zone uses the first active branch flagged
"receives online orders" that has coordinates and a phone. A branch with no
phone number cannot be a pickup point, and the API log says so by reference.

These three are the only ones worth setting by hand. The rest are written to
`.env` with a literal fallback in the deploy workflow, so leaving them unset
gives the intended value rather than an empty one:

| Secret | Falls back to | Notes |
|--------|---------------|-------|
| `LALAMOVE_ENV` | `production` | Sandbox has no working AE pricing engine and an unfunded wallet |
| `LALAMOVE_MARKET` | `AE` | |
| `LALAMOVE_LANGUAGE` | `en_AE` | Lalamove validates this to exactly this string for the UAE |
| `LALAMOVE_SERVICE_TYPE` | `CAR` | Smallest UAE vehicle |
| `LALAMOVE_SPECIAL_REQUESTS` | *(empty)* | Was `DOOR_TO_DOOR` until Aug 2026, a flat +5 AED per order for a promise drivers keep anyway |
| `LALAMOVE_WEBHOOK_PATH` | `/api/v1/webhooks/lalamove` | Must match the Partner Portal URL byte for byte — it is part of the signature |
| `LALAMOVE_TIMEOUT_SECONDS` | `8` | |
| `LALAMOVE_QUOTE_CACHE_SECONDS` | `120` | |
| `BATCH_DISPATCHER_ENABLED` | `true` | The in-process loop that sends a batch when its window closes |

**The fallbacks are load-bearing, not tidiness.** An unset secret expands to an
empty string, and an empty value in `.env` overrides the Python default rather
than deferring to it — an empty `LALAMOVE_TIMEOUT_SECONDS` fails float parsing
and the API does not boot. The two credentials are deliberately *not*
defaulted: empty there means "no courier", which the code is built to handle.

Setting the two credentials:

```bash
gh secret set LALAMOVE_API_KEY --repo hussu97/mm-ecommerce
gh secret set LALAMOVE_API_SECRET --repo hussu97/mm-ecommerce
```

Two things have to be done in the Partner Portal, not here:

1. **Webhook URL** → `https://api.meltingmomentscakes.com/api/v1/webhooks/lalamove`,
   Webhook Version 3. The path is part of what the signature covers, so it must
   match `LALAMOVE_WEBHOOK_PATH` byte for byte. An endpoint that fails to answer
   200 ten times in a day is disabled by Lalamove, after which no order gets a
   status until someone re-enters the URL.
2. **Fund the wallet.** Orders debit it the moment they are placed; an empty
   wallet fails dispatch with `ERR_INSUFFICIENT_CREDIT`. The failure is recorded
   on the order and surfaced in the admin, and the order can be re-dispatched
   once topped up — but nothing is collected in the meantime.

#### Cloudflare Turnstile — the bot check on signup

Two endpoints make us send mail to an address the caller typed: `/auth/register`
and `/auth/forgot-password`. Between April and August a bot used both to send a
welcome and a reset — seven to eighty seconds apart — to eighteen harvested
addresses. Rate limiting was already in place and irrelevant: `5/minute` per IP
stops a burst, and this was one signup every few hours from somewhere new. The
cost is not to us directly but to the people receiving unsolicited mail from our
domain, and to the sending reputation that eventually gets throttled for it.

Set it up at **dash.cloudflare.com → Turnstile → Add site**, domain
`meltingmomentscakes.com`, widget mode **Managed** (invisible to almost
everyone). It is free and needs no Cloudflare plan.

| Where | Key | Notes |
|-------|-----|-------|
| GitHub secret, `mm-ecommerce` | `FIREBASE_PROJECT_ID` | `melting-moments-cakes`. Not secret, but it is the audience check. `gh secret set FIREBASE_PROJECT_ID --repo hussu97/mm-ecommerce` |
| Vercel env, storefront | `NEXT_PUBLIC_FIREBASE_API_KEY` | `AIzaSyC38DuQCuPjs-jK04f1YQF4wQdoCBAvPxE`. Public by design; restricted by HTTP referrer and to the AE SMS region. |
| Vercel env, storefront | `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN` | `melting-moments-cakes.firebaseapp.com` |
| Vercel env, storefront | `NEXT_PUBLIC_FIREBASE_PROJECT_ID` | `melting-moments-cakes` |
| Vercel env, storefront | `NEXT_PUBLIC_FIREBASE_APP_ID` | `1:136865397988:web:9012ddfa2418fb8ac280f1` |
| GitHub secret, `mm-ecommerce` | `TURNSTILE_SECRET_KEY` | The private half. `gh secret set TURNSTILE_SECRET_KEY --repo hussu97/mm-ecommerce` |
| Vercel, **web** project | `NEXT_PUBLIC_TURNSTILE_SITE_KEY` | The widget half. Public by design — it identifies the site, it authorises nothing |

**Both halves, or neither.** Empty disables the check at both ends: the
storefront renders no widget and the API asks for no token. That is deliberate —
shipping the code before the keys exist must not lock real customers out of
signing up. It also means setting only the secret would refuse every signup,
because no page would be producing a token. Set the Vercel variable first, then
the GitHub secret, then redeploy.

Cloudflare's test keys are worth knowing: site `1x00000000000000000000AA` with
secret `1x0000000000000000000000000000000AA` always passes, and
`2x0000000000000000000000000000000AA` always fails. The secrets are answered
locally without touching the network, so a test suite exercises the wired-up
path without one.

A signed-in customer asking for a reset link to **their own** address is not
challenged — they hold a session for that mailbox, which proves more than a
widget could, and the account settings screen has no widget on it.

If Cloudflare is unreachable the request goes through and the log says so. Their
outage should not become ours.

#### Courier — noon Send (Rider-on-Demand)

Optional, and safe to leave unset: a `noon_send` zone with no credentials simply
dispatches through Lalamove. Same for anything noon Send refuses — past their
distance cap, outside the fleet area, or nobody free — so this can never strand
an order.

Only the `Sharjah Central` zone uses it. noon Send cannot cross an emirate
boundary and the kitchen is in Sharjah, so Ajman and Dubai are not candidates
however the map is redrawn.

> **Read this before deploying.** **Every** order in a `noon_send` zone now goes
> to noon Send. There is no allow-list and no trial account — the polygon is the
> whole decision, and anything noon Send refuses falls back to Lalamove
> automatically.
>
> That makes `NOON_SEND_ENV` the only thing between a customer's cake and a real
> rider, so **it and `NOON_SEND_API_KEY` must name the same fleet**. A `staging`
> task is created, tracked and cancelled for real and is collected by nobody —
> which was survivable when one known account was routed there and is not
> survivable now. Both default to production; set them together or not at all.
| `NOON_SEND_WEBHOOK_API_KEY` | a secret you generate | Hand the same value to the integrations team with the webhook URLs |
| `MAPBOX_ACCESS_TOKEN` | a `pk.` token from account.mapbox.com | **Optional.** Drives "driver 6 min · 4.2 km away" on the register and the admin. Empty falls back to a straight-line estimate with no ETA — nothing breaks. Directions needs no scope, so leave every secret scope unticked; and leave the URL restriction **empty**, because Mapbox enforces it on the `Referer` header that a server request does not send. |
| `NOON_SEND_ENFORCE_WEBHOOK_KEY` | `false` | Whether a push missing that key is refused. Leave off until the key noon actually sends matches the one above — compare the two fingerprints on any `webhook_logs` row. Enforcing before they agree discards live delivery updates, which is what happened during the trial. |

The rest fall back in the deploy workflow:

| Secret | Falls back to | Notes |
|--------|---------------|-------|
| `NOON_SEND_ENV` | `production` | The fleet a task is created against. `staging` creates real tasks that no rider collects — see the note above |
| `NOON_SEND_LOCALE` | `en-ae` | |
| `NOON_SEND_CLIENT_CODE` | `noon_food` | `noon_food` or `nownow` |
| `NOON_SEND_MAX_DISTANCE_M` | `20000` | Matches the 20 km the rate card prices to and the radius `Sharjah Central` is drawn at. Never set it tighter than the zone — see the note above. `GET /public/v1/configurations` reports the real per-partner limit |
| `NOON_SEND_DETOUR_FACTOR` | `1.49` | Straight line to road distance. Only used to estimate cost — there is no quotation API |
| `NOON_SEND_TIMEOUT_SECONDS` | `8` | |

```bash
gh secret set NOON_SEND_API_KEY --repo hussu97/mm-ecommerce
gh secret set NOON_SEND_WEBHOOK_API_KEY --repo hussu97/mm-ecommerce
gh secret set MAPBOX_ACCESS_TOKEN --repo hussu97/mm-ecommerce
```

#### GrubOps (aggregator out-of-stock sync)

Mirrors "mark out of stock" from the register onto Noon, Talabat and Deliveroo,
so the shop says it once instead of twice. One way only — this app is the source
of truth and GrubOps is told.

GrubTech publish no partner API, so the integration signs in as a console user
against their Cognito pool. `GRUBOPS_PASSWORD` is a real password: it goes in
secrets, never in the repo, and rotating it in the GrubOps console means setting
it here and redeploying.

> **Leave `GRUBOPS_SYNC_ENABLED` false until the item map is seeded and
> approved.** The map is built by matching item names, which is a guess until a
> human confirms it in the admin console (Integrations → GrubOps). Turning the
> sync on with a half-built map takes the wrong things off the aggregators.
> Nothing is ever pushed for an unapproved row, so shipping it off and seeding
> at leisure is safe.

| Secret | Falls back to | Notes |
|--------|---------------|-------|
| `GRUBOPS_SYNC_ENABLED` | `false` | The kill switch. Off until the map is approved |
| `GRUBOPS_USERNAME` | — | The GrubOps console login |
| `GRUBOPS_PASSWORD` | — | That login's password |
| `GRUBOPS_PARTNER_ID` | `6922fe267f5b1c6d208c634f` | This account's partner id |
| `GRUBOPS_COGNITO_CLIENT_ID` | `2d8lmtmc241sviat2psomuuon8` | Their app client; changes only if GrubTech reissue it |
| `GRUBOPS_COGNITO_REGION` | `eu-west-2` | |
| `GRUBOPS_API_BASE` | `https://internal-api.grubtech.io` | Availability writes |
| `GRUBOPS_CATALOG_API_BASE` | `https://api-grubone.grubtech.io` | Brands and menu listing — a different host, which is their split |
| `GRUBOPS_SOURCE` | `grubOps 2.0` | Stamped on every record we write, and deliberately the same string their own console stamps. Leave it alone |
| `GRUBOPS_TIMEOUT_SECONDS` | `8` | |
| `GRUBOPS_RECONCILE_TICK_SECONDS` | `120` | How often the loop recomputes and pushes differences |
| `GRUBOPS_ORDERS_ENABLED` | `false` | Aggregator order ingest kill switch. Off until watched once in prod |
| `GRUBOPS_ORDERS_API_BASE` | `https://api-grubops.grubtech.io` | The console host orders answer on |
| `GRUBOPS_ORDERS_TICK_SECONDS` | `60` | How often the ingest loop polls GrubOps for orders |

```bash
gh secret set GRUBOPS_USERNAME --repo hussu97/mm-ecommerce
gh secret set GRUBOPS_PASSWORD --repo hussu97/mm-ecommerce
gh secret set GRUBOPS_SYNC_ENABLED --repo hussu97/mm-ecommerce
```

#### Slider (the third courier)

Slider is gated to one account while the pilot runs. Six zones on the map name
it — `Sharjah Core`, `Ajman City`, the three Dubai bands and `Umm al-Quwain
City` — and for every customer who is **not** on `SLIDER_TRIAL_EMAILS` those
zones resolve to exactly the courier that carried them before: noon Send inside
Sharjah, Lalamove everywhere else. So publishing the map and shipping the code
is a no-op for every customer but one, deliberately.

> **`SLIDER_TRIAL_EMAILS` is one switch with two halves.** Setting it starts the
> pilot: Slider carries that account's orders, *and* its delivery is free
> anywhere it can be delivered to. Emptying it ends both together — Slider opens
> to its zones and nobody gets free delivery. It is matched against a
> **signed-in** customer's own account email; a guest typing the same address
> never qualifies. It is deliberately not tied to `APP_ENV` or `SLIDER_ENV`,
> because an environment-shaped gate opens a trial to everybody the moment the
> environment changes.
>
> The waiver zeroes what the **customer** pays and nothing else. `quoted_cost`
> and `cost_total` on `order_deliveries` still record what Slider charged us, so
> cost the pilot from those two columns — the margin figure shows every pilot
> order as fully negative, correctly.

| Secret | Value | Notes |
|--------|-------|-------|
| `SLIDER_API_KEY` | from the Slider dashboard | `sk_test_…` for the sandbox, `sk_live_…` for production — swap it and `SLIDER_ACCOUNT_ID` together with `SLIDER_ENV`. Sent as `X-Slider-Key` — their API ignores a Bearer token, and answers one exactly as it answers no credentials at all. Empty means every Slider zone falls back, exactly as an empty Lalamove or noon Send key does. Not an outage |
| `SLIDER_ACCOUNT_ID` | from the Slider dashboard | Sent in the request **body** as `account_id`; an `X-Account-Id` header is ignored. **Environment-specific** — the sandbox account is not the production account, so this changes when `SLIDER_ENV` does, in step with the key |
| `SLIDER_WEBHOOK_TOKEN` | a secret you generate | Set the same value in Slider's dashboard. **Enforced** — an empty token rejects every push, unlike `NOON_SEND_ENFORCE_WEBHOOK_KEY` |
| `SLIDER_STAGING_WEBHOOK_TOKEN` | a second secret you generate | For the staging webhook, which is pointed at production on purpose. See below |
| `SLIDER_TRIAL_EMAILS` | empty until the pilot starts | Then `h_abbasi97@hotmail.com`. Comma-separated |

The rest fall back in the deploy workflow:

| Secret | Falls back to | Notes |
|--------|---------------|-------|
| `SLIDER_ENV` | `staging` | `staging` = `https://api-sandbox.slider-app.com/v1`, `production` = `https://api.slider-app.com/v1`, or an absolute `https://` origin which wins over both. Both confirmed live 2026-08-21 (`POST /v1/deliveries/fare` answers 401 unauthenticated). The override stays for the day those move — a wrong host fails as DNS on the first real booking |
| `SLIDER_TIMEOUT_SECONDS` | `8` | |
| `SLIDER_BIKE_MAX_KM` | `35` | **Road** kilometres, and half of the vehicle rule: a bike only if the drop is in the *same emirate* as the kitchen **and** within this. See the open question below |
| `SLIDER_DETOUR_FACTOR` | `1.44` | Straight line to road distance, measured over the 97 areas of the fare survey. Used only when Slider has not told us a distance itself |
| `SLIDER_WEBHOOK_HEADER` | `X-Slider-Token` | Must match the "Token Header Key" field in Slider's dashboard, which ships **empty** — set it explicitly or the token may never be sent |
| `SLIDER_STAGING_WEBHOOK_HEADER` | `X-Slider-Token` | Same, for the staging webhook |

```bash
gh secret set SLIDER_API_KEY --repo hussu97/mm-ecommerce
gh secret set SLIDER_ACCOUNT_ID --repo hussu97/mm-ecommerce
gh secret set SLIDER_WEBHOOK_TOKEN --repo hussu97/mm-ecommerce
gh secret set SLIDER_STAGING_WEBHOOK_TOKEN --repo hussu97/mm-ecommerce
gh secret set SLIDER_TRIAL_EMAILS --repo hussu97/mm-ecommerce
```

> **Blocked on Slider, 2026-08-21 — the production VM's IP is refused.** The
> API answers `34.18.98.2` (the GCP VM) with a `403` nginx page for the same
> request it answers from elsewhere with a normal `401` JSON — credentials or
> no credentials, so it is the source IP and not the key. **Slider must
> allowlist `34.18.98.2`** before a booking can succeed from production.
>
> Do not read this 403 as the `User-Agent` one described in the provider's
> module docstring. That 403 is cured by sending a UA; this one persists with
> it. Two failures of the same shape and different causes.

**Two things to fix in the Slider dashboard first.**

1. The **staging Webhook URL** is an email address (`h_abbasi97@hotmail.com`) in
   a URL field. Nothing will ever be delivered to it. It needs to be
   `https://<prod-host>/api/v1/webhooks/slider/staging`.
2. **Both Token Header Key fields are empty.** A token with no header key may
   not be sent at all, or may arrive in a header we are not reading. Set both to
   `X-Slider-Token`, matching `SLIDER_*_WEBHOOK_HEADER`.

The production webhook goes to `https://<prod-host>/api/v1/webhooks/slider`. The
staging one is pointed at production on purpose: `/api/v1/webhooks/slider/staging`
acknowledges the push, writes a `webhook_logs` row and does **nothing else** — no
`webhook_events`, no `order_deliveries`, no order lookup. Real Slider traffic can
then be watched in Admin → Webhook logs (`provider = slider`, `endpoint =
staging`) while the integration is still being proved. `LOG_RETENTION_DAYS` is 7,
so it is a window rather than an archive.

**Deploy order.** Each step is independently reversible:

1. `alembic upgrade 126_cost_banded_map_v2` — the re-split map, every zone still
   naming the courier that carries it today, and every zone carrying the
   alternates `125_zone_alternates` gave the map before it.
2. Ship the Slider code with `SLIDER_API_KEY` and `SLIDER_TRIAL_EMAILS` empty.
   `alembic upgrade 127_slider_courier` registers the courier row.
3. `alembic upgrade 128_slider_zones` — the six zones name Slider, and their
   alternates move with them. Still a no-op; nobody is on the list. The zones
   **keep** the Lalamove runs they were riding, which is what keeps their
   arrival promise and their batching identical for everybody off the list.
4. Set `SLIDER_API_KEY` and `SLIDER_ACCOUNT_ID`.
5. Set `SLIDER_TRIAL_EMAILS`. The pilot begins, one account, and that one
   setting grants both halves at once.
6. Empty the list to end it.

> **Verified end-to-end 2026-08-21 — create, fetch and cancel all work.** Run
> against the sandbox from a permitted IP: `POST /deliveries` returned
> `order_number` 62056867, `GET /deliveries/{order_number}` read it back, and
> `DELETE /deliveries/{order_number}` cancelled it. The booking path is proven,
> not merely spec-conformant.
>
> **They validate `vehicle_type` and then override it.** `banana` is refused
> with a 422 ("The selected vehicle type is invalid"), but `bike` and `any`
> both came back as `car`, priced as a car — on a route their own fare call had
> just offered a bike for. So the vehicle we ask for is a request, never a
> fact. `dispatch_order` records `vehicle_type` off the **create response** and
> prices off its `fare` for that reason: pricing off the tier we asked for
> would book a car, record a bike, and under-record the cost by the difference
> (AED 3.98 on the measured run). Whether this is a sandbox artefact or their
> real behaviour is worth asking them — it decides whether the bike tier is
> reachable at all, and with it whether the emirate rule below matters.

> **Answered 2026-08-21 — the bike does cross an emirate boundary.** Their live
> sandbox quotes Sharjah→Ajman (15.27 km) as `{"vehicle_type": "bike",
> "is_available": true, "delivery_fee": 19.62}`, and their published reference
> names exactly one bike constraint: a 35 km ceiling, with no emirate rule at
> all. `slider_service.vehicle_for` still refuses a cross-emirate bike, so we
> are booking cars we are not required to book — about AED 4 an order on this
> route. **Left unchanged deliberately**: it moves what a customer is quoted and
> which vehicle carries a cake, which is a commercial call, not a bug fix.
>
> **The API reference is `https://partners.slider-app.com/docs`.** Read it before
> changing anything here. Four separate field-level bugs in the first cut of this
> integration were all of the same kind — a plausible guess where a published
> name existed.

> **Open question, worth answering before step 5.** Can Slider's bike cross an
> emirate boundary? Their `/deliveries/fare` says yes — it offered a bike for
> Sharjah→Ajman at 12 km and for nine Sharjah→Dubai routes out to 34.4 km. The
> code assumes no. It is worth about AED 5 an order across Ajman and Dubai, and
> if the answer is yes the only change is in `slider_service.vehicle_for`.
>
> **Resolved 2026-08-21 — the hostnames.** They were guessed as
> `api.staging.slider.ae` / `api.slider.ae`, and the whole `slider.ae` zone
> SERVFAILs, apex included: every booking would have failed as DNS. The real
> pair is `https://api-sandbox.slider-app.com/v1` and
> `https://api.slider-app.com/v1`, now the defaults in
> `slider_provider.HOSTS`. Note the `/v1`: `_call` concatenates rather than
> `urljoin`, so a versioned base keeps its prefix.

**Registering a branch.** Which outlet a rider collects from is a property of the
branch, not of the deployment — Lalamove already reads the pickup coordinates and
phone from the same row. Each kitchen is registered with noon Send separately and
keeps its own code:

```bash
python -m scripts.register_noon_send_pickup                          # report all
python -m scripts.register_noon_send_pickup --branch K001 --create   # register one
```

The script reads the branch's pin, address and phone, refuses a location noon Send
says it cannot reach, and writes the returned code into
`branches.noon_send_outlet_code`. It is also visible and editable at
**Admin → Branches → noon Send outlet code**, which is where you would paste a code
issued out of band.

A branch with no code simply does not dispatch through noon Send — its orders fall
back to Lalamove, and the delivery row says which branch is missing a code. So when
Barsha Heights starts delivering, it is one script run and no deploy.

**What noon Send promises** is no longer part of a deploy. It lives in
`couriers.unbatched_promise_minutes` and is edited at **Admin → Delivery Zones →
Estimates**, alongside every batch group's minutes-to-door and the number of days
a third-party courier takes. On the release that introduces that screen — or from
a shell, when the change should land with the deploy rather than after it:

```bash
python -m scripts.set_courier_promise                                  # report all
python -m scripts.set_courier_promise --code noon_send --minutes 90    # 60 -> 90
python -m scripts.set_courier_promise --code third_party --days 2      # not next-day
```

Deliberately a script and not a migration: these are commercial figures, and a
migration that set one would re-set it on any environment restored from an older
dump — silently reverting whatever the shop had since chosen. Nothing already
quoted moves; what the shop said out loud is a record, and only the next promise
reads the new number.

**Closing a branch for a day** is **Admin → Branches → Holidays**. Whole days
only — a branch opening late is a change to its trading hours, which are fields
on the branch itself. Weekends are ordinary working days; only a dated row closes
the shop. The delivery estimate reads them, so a closure moves what customers in
that branch's zones are quoted from the next request onward: a batch run waits
for the next open day, a third-party handover does too, and a courier promising
minutes starts its clock at the next opening rather than now.

Two things have to be done by the noon RoD integrations team, not here:

1. **Register the webhooks.** Status →
   `https://api.meltingmomentscakes.com/api/v1/webhooks/noon-send`, rider
   tracking → `.../api/v1/webhooks/noon-send/tracking`.

   **Their staging environment sends no `X-API-Key`** — there is nowhere in it
   to configure one — so a keyless push is accepted. A push carrying the *wrong*
   key is still refused, which catches the realistic mistake of their production
   side holding a stale one. What guards the endpoint meanwhile is the task
   number: a push only moves an order we already dispatched under that
   `mp_task_nr`, and anything else is acknowledged and ignored. Give them
   `NOON_SEND_WEBHOOK_API_KEY` for production, where they can send it. Unlike Lalamove the
   path is not part of any signature, so it can be changed freely. The URLs are
   registered per environment, so moving to the production fleet means giving
   them to the integrations team again.
2. **The distance limit answers itself.** `GET /public/v1/configurations`
   reports the real cap for whichever key is configured, and the code now asks:
   `noon_send_service.max_distance_m()` takes the stricter of their number and
   `NOON_SEND_MAX_DISTANCE_M`, so their answer can only narrow the zone, never
   silently widen one nobody has redrawn.

   The three numbers that used to disagree: the integration doc says the
   standard partner limit is 15 km, the rate card prices bands out to 20, and
   the staging key answers **50 km**. Only the API is about our own account.
   Check production's with:

   ```bash
   curl -s -H "X-API-Key: $NOON_SEND_API_KEY" -H "X-Locale: en-ae" \
     https://food-api-team.noon.team/public/v1/configurations
   ```

   If it comes back under 20 km, nothing breaks — the guard narrows, those pins
   fall back to Lalamove at the same fee to the customer, and the only loss is
   the saving on the outer ring. To reclaim it, agree a wider cap with the
   commercial team (`sbhatti@noon.com`). To match the map to a permanently
   narrower cap instead, shrink `INNER_ZONE` in
   `scripts/build_delivery_zones.py` and republish: the guard and the radius
   have to move together or the zone claims addresses it cannot serve.

   The same call reports `cod_limit` and `prepaid_limit` in fils. **Production
   answers AED 500 and AED 2,500** (partner 135208, read live on 2026-08-05);
   staging said AED 300 and AED 5,000, so the prepaid ceiling went *down* by
   half on the real fleet. An order over AED 2,500 — a large wedding cake is not
   out of reach — is refused by `may_serve` and carried by Lalamove instead,
   which is correct but worth knowing before someone reports it as a bug. The
   numbers are read from the API at runtime, so this note is a record rather
   than a setting. `may_serve` refuses an order over either
   rather than letting task creation reject it.

There is no wallet to fund — billing is on the partner agreement. Once
`NOON_SEND_ENV=production`, orders are real: a rider is dispatched and a
cancellation is charged.

#### Apple Push — waking the POS registers

The APNs auth key is **team-scoped and account-wide**: one key serves every app
in the Apple team and both the sandbox and production hosts, and Apple caps an
account at two. So this is the *same* key the other apps already use — Key ID
`CWXGV3TWNY`, Team `2F94NY8R3T`, local copy at `~/.apns/AuthKey_CWXGV3TWNY.p8`.
Apple serves the `.p8` exactly once, at creation; if that file is lost the key
has to be reissued.

Empty means no push. Orders still arrive and the register still shows them when
it polls, so a missing key is a quieter shop rather than a broken one.

| Secret | Value | Notes |
|--------|-------|-------|
| `APNS_KEY_P8` | the `.p8`, **one line** | See the conversion below. A multi-line secret does not survive the `printf` into `.env` |
| `APNS_KEY_ID` | `CWXGV3TWNY` | The ten characters shown beside the key in the Apple portal |
| `APNS_TEAM_ID` | `2F94NY8R3T` | The `OU` of a signing certificate — *not* the ten characters in its common name |
| `APNS_ORDER_SOUND` | falls back to `new-order.caf` | Bundled in the app |
| `APNS_TIMEOUT_SECONDS` | falls back to `10` | |

```bash
# Flatten the PEM to one line with literal \n, then set it.
awk 'BEGIN{ORS="\\n"} {print}' ~/.apns/AuthKey_CWXGV3TWNY.p8 \
  | gh secret set APNS_KEY_P8 --repo hussu97/mm-ecommerce
gh secret set APNS_KEY_ID  --repo hussu97/mm-ecommerce   # CWXGV3TWNY
gh secret set APNS_TEAM_ID --repo hussu97/mm-ecommerce   # 2F94NY8R3T
```

One thing that must be done in the Apple portal, not here: **the Push
Notifications capability has to be enabled on both App IDs** —
`com.meltingmoments.pos` (the iPad register) and `com.meltingmoments.posmanager`
(the phone companion). Without it every send fails with `DeviceTokenNotForTopic`,
which does not mention the capability.

A 403 from APNs is almost always one of three things and never the device: the
wrong Key ID, the wrong Team ID, or the App Store Connect `.p8` instead of the
push one. Both download as `AuthKey_<ID>.p8` and nothing inside the file
distinguishes them.

#### Frontend URLs (used in email templates & CORS)

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `WEB_URL` | `https://meltingmomentscakes.com` | Literal |
| `ADMIN_URL` | `https://admin.meltingmomentscakes.com` | Literal |

#### Backups & Observability

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `BACKUP_GCS_BUCKET` | `melting-moments-cakes-backups` | GCS bucket created in Step 8 |
| `GCP_PROJECT_ID` | `melting-moments-cakes` | Used by the `gcplogs` Docker driver to ship API logs to Cloud Logging. On GCE this is auto-detected — set it anyway so the `.env` write step is explicit. |
| `SENTRY_DSN` | `https://...@sentry.io/...` | DSN from Sentry project `mm-backend`. Used by the ecommerce API container. Frontend DSNs are configured directly in Vercel. |

#### Log retention

Everything here has a working default; a secret is only needed to change one.

| Secret | Falls back to | Notes |
|--------|---------------|-------|
| `LOG_RETENTION_DAYS` | `7` | Covers `webhook_logs`, `email_logs` and `webhook_events`. `webhook_logs` is the fastest-growing table in the database — noon Send push a rider position every 15-30 seconds per live task and every one is stored at full payload — so this bound is what makes that completeness affordable. It now also holds every Stripe and Ziina webhook; no payment history is lost when a row ages out, because `webhook_events` keeps the dedup ledger and `payment_transactions` keeps the outcome, both permanently. What ages out is the raw body |
| `AUDIT_RETENTION_DAYS` | `90` | Covers `audit_logs` only, and deliberately much longer. That table is not debugging output but the record of who changed what, and it is wanted exactly when somebody disputes a change weeks after it happened |

Swept hourly by a loop inside the API's own lifespan (`app/services/log_retention.py`),
which holds a Postgres advisory lock so only one worker in the deployment ever runs
one. There is no cron in this stack, and nothing that survives the container.

#### Analytics (optional — leave empty to disable)

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `UMAMI_API_KEY` | API key | Umami Cloud dashboard → Settings → API Keys |
| `UMAMI_WEBSITE_ID` | website UUID | Umami Cloud dashboard → website settings |

**Rollback**: If a bad deploy reaches production, trigger the `rollback.yml` workflow manually from GitHub Actions → dispatch. It accepts a git SHA (defaults to `HEAD~1`) and optionally runs `alembic downgrade -1` before rolling back.

---

## Step 14: Post-Deployment Smoke Test

Run these checks after completing all steps:

```bash
# API health check (verifies DB connectivity too)
curl https://api.meltingmomentscakes.com/health
# Expected: {"status": "ok", "service": "mm-api", "env": "production"}

# All backend containers healthy
ssh <VM> "docker compose -f /opt/melting-moments-cakes/docker-compose.prod.yml ps"
# Expected services: redis, postgres, api, nginx, certbot — all Up

# Backup script
ssh <VM> "DEPLOY_DIR=/opt/melting-moments-cakes /opt/melting-moments-cakes/scripts/backup-db.sh"
# Should print "Deployment complete" and upload to GCS

# SSL certificate valid
echo | openssl s_client -connect api.meltingmomentscakes.com:443 2>/dev/null | openssl x509 -noout -dates

# Vercel deployments
# Visit https://meltingmomentscakes.com — storefront loads
# Visit https://admin.meltingmomentscakes.com — admin login loads
```

---

## Ongoing Maintenance

**Deployments**: Push to `main` → GitHub Actions SSHes into GCP VM and runs `deploy.yml` (API only). Vercel auto-deploys web + admin.

**Manual deploy**: SSH into VM and run `DEPLOY_DIR=/opt/melting-moments-cakes bash scripts/deploy.sh`.

**SSL renewal**: Handled automatically by the `certbot` container (runs every 12 hours).

**Database backups**: `hussainabbasi786110`'s cron runs `scripts/backup-db.sh` at
02:00 UTC daily, keeping 7 days locally in `/opt/melting-moments-cakes/backups`
and 90 days in `gs://melting-moments-cakes-backups` (aged out by a bucket
lifecycle rule). `deploy.yml` takes an extra one before every migration. Verify
offsite copies are still arriving with `gcloud storage ls -l
gs://melting-moments-cakes-backups/backups/` — the local dumps existing proves
nothing about the offsite ones, which is exactly how their absence went unnoticed
for five months.

**Disk snapshots**: `mm-backend-daily-snapshot` snapshots the boot disk at 03:00
UTC, 14-day retention. This is what covers losing the VM itself; the dumps live
on the same disk as the database and cannot.

**Disk housekeeping**: `deploy.yml` prunes images and build cache older than 12h
on every deploy, and caps the systemd journal at 200 MB. Container logs are
capped by `/etc/docker/daemon.json` (`max-size: 10m`, `max-file: 3`) — without it
a long-lived container's json log grows without bound, which one had done to
27 MB. That file applies to containers created *after* it is written, so a
container that predates it keeps its uncapped log until recreated.

**Scaling**: If the e2-micro becomes a bottleneck, upgrade in-place: `gcloud compute instances set-machine-type mm-backend --machine-type=e2-small --zone=me-central1-a` (requires VM stop/start).
