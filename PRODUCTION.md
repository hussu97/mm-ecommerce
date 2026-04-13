# Production Deployment Guide — Melting Moments Ecommerce

## Architecture

```
Internet
├── meltingmomentscakes.com        → Vercel (web storefront)
├── admin.meltingmomentscakes.com  → Vercel (admin panel)
├── api.meltingmomentscakes.com    → GCP VM: FastAPI via Nginx + SSL
└── media.meltingmomentscakes.com  → Cloudflare R2 (object storage)
```

**GCP VM** (e2-micro, 1 vCPU shared, 1 GB RAM) runs:
- PostgreSQL 16
- Redis 7 (response caching)
- FastAPI (Uvicorn)
- Nginx (reverse proxy for `api.*`)
- Certbot (SSL for `api.*`)

**Vercel** hosts both Next.js apps — free Hobby plan, global CDN, automatic deployments on push to `main`.

---

## Monthly Cost Estimate

| Service | Provider | Plan | Est. $/mo |
|---------|----------|------|-----------|
| Web storefront (Next.js) | Vercel | Hobby (free) | $0 |
| Admin panel (Next.js) | Vercel | Hobby (free, same account) | $0 |
| Backend VM (e2-micro) | GCP Compute Engine | On-demand + sustained discount | ~$4 |
| Boot disk (20 GB SSD) | GCP Persistent Disk | Standard SSD | ~$5 |
| Database backups | GCP Cloud Storage | Standard, ~2 GB | ~$0.05 |
| Network egress | GCP | ~5 GB/mo | ~$0.40 |
| Media storage | Cloudflare R2 | Free tier (10 GB) | $0 |
| SSL certificates | Let's Encrypt | Free | $0 |
| Analytics | Umami Cloud | Free tier | $0 |
| **Total** | | | **~$9–10/mo** |

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

## Step 8: GCP Cloud Storage Bucket + Service Account

```bash
# Create backup bucket (in the same region as the VM)
gcloud storage buckets create gs://melting-moments-cakes-backups \
  --project=melting-moments-cakes \
  --location=ME-CENTRAL1 \
  --uniform-bucket-level-access

# Create a service account for backups
gcloud iam service-accounts create mm-backup-sa \
  --project=melting-moments-cakes \
  --display-name="MM Backup Service Account"

# Grant it permission to write to the bucket only
gcloud storage buckets add-iam-policy-binding gs://melting-moments-cakes-backups \
  --member="serviceAccount:mm-backup-sa@melting-moments-cakes.iam.gserviceaccount.com" \
  --role="roles/storage.objectCreator"

# Attach the service account to the VM
gcloud compute instances set-service-account mm-backend \
  --project=melting-moments-cakes \
  --zone=me-central1-a \
  --service-account=mm-backup-sa@melting-moments-cakes.iam.gserviceaccount.com \
  --scopes=https://www.googleapis.com/auth/devstorage.read_write
```

Then set `BACKUP_GCS_BUCKET=melting-moments-cakes-backups` in `.env`.

---

## Step 9: Schedule Automated Backups (Cron)

```bash
# Make backup script executable
chmod +x /opt/melting-moments-cakes/scripts/backup-db.sh

# Open crontab
crontab -e
```

Add this line (runs daily at 2 AM):
```
0 2 * * * DEPLOY_DIR=/opt/melting-moments-cakes /opt/melting-moments-cakes/scripts/backup-db.sh >> /var/log/mm-backup.log 2>&1
```

Test it manually:
```bash
DEPLOY_DIR=/opt/melting-moments-cakes /opt/melting-moments-cakes/scripts/backup-db.sh
# Verify file appears in GCS:
gsutil ls gs://melting-moments-cakes-backups/backups/
```

---

## Step 9b: Cloudflare Tunnel (temporary — no domain yet)

If you don't have the final domain yet, use a free Cloudflare Quick Tunnel to expose the API over HTTPS.

```bash
# Install cloudflared on the VM
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# Run in background (survives SSH disconnect)
nohup cloudflared tunnel --url http://localhost:8000 > ~/cloudflared.log 2>&1 &

# Get the public URL
tail -f ~/cloudflared.log
# Look for a line like: https://xxxx-xxxx-xxxx.trycloudflare.com
```

Use the printed URL as `NEXT_PUBLIC_API_URL` in Vercel — **with** the `/api/v1` suffix:
```
NEXT_PUBLIC_API_URL=https://xxxx-xxxx-xxxx.trycloudflare.com/api/v1
```

> **Note:** The tunnel URL changes every time `cloudflared` restarts. Update the Vercel env var and redeploy when that happens. Once you have the real domain, follow Step 12 and use `https://api.meltingmomentscakes.com` instead.

Also update `.env` on the VM to allow the Vercel preview URLs in CORS:
```env
CORS_ORIGINS=["https://<web>.vercel.app","https://<admin>.vercel.app"]
ALLOWED_HOSTS=["*"]
WEB_URL=https://<web>.vercel.app
ADMIN_URL=https://<admin>.vercel.app
```

Restart the API after updating `.env`:
```bash
docker compose -f docker-compose.prod.yml up -d api
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
   NEXT_PUBLIC_UMAMI_URL=/umami/script.js
   NEXT_PUBLIC_SENTRY_DSN=<from Sentry project settings>  # optional
   ```
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
   NEXT_PUBLIC_API_URL=https://api.meltingmomentscakes.com/api/v1
   ```
4. Click **Deploy** and verify the preview URL
5. Go to **Settings → Domains** → add `admin.meltingmomentscakes.com`

---

## Step 12: DNS Configuration

In Cloudflare (or your registrar), create these records:

| Type | Name | Value | Notes |
|------|------|-------|-------|
| A | `@` | GCP VM external IP | Web storefront root |
| CNAME | `www` | `cname.vercel-dns.com` | Vercel redirect |
| CNAME | `admin` | `cname.vercel-dns.com` | Admin panel |
| A | `api` | GCP VM external IP | FastAPI |
| CNAME | `media` | `<your-r2-bucket>.r2.cloudflarestorage.com` | R2 media |

> **Vercel custom domains**: After adding a custom domain in Vercel, it will show you the exact DNS record needed (either A or CNAME depending on apex vs subdomain). Follow those instructions; the values above are typical.

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

#### Stripe

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `STRIPE_SECRET_KEY` | `sk_live_...` | Stripe dashboard → Developers → API keys |
| `STRIPE_PUBLISHABLE_KEY` | `pk_live_...` | Stripe dashboard → Developers → API keys |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` | Stripe dashboard → Developers → Webhooks → signing secret |

#### Email (Resend)

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `RESEND_API_KEY` | `re_...` | Resend dashboard → API Keys |
| `FROM_EMAIL` | `orders@meltingmomentscakes.com` | Must match a verified Resend sending domain |

#### Cloudflare R2 (media storage)

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `CLOUDFLARE_R2_ACCESS_KEY` | R2 token access key | Cloudflare dashboard → R2 → Manage R2 API Tokens |
| `CLOUDFLARE_R2_SECRET_KEY` | R2 token secret key | Same page as above |
| `CLOUDFLARE_R2_BUCKET` | `melting-moments-cakes` | Literal |
| `CLOUDFLARE_R2_ENDPOINT` | `https://<account_id>.r2.cloudflarestorage.com` | Cloudflare dashboard → R2 → bucket → Settings |
| `CLOUDFLARE_R2_PUBLIC_URL` | `https://media.meltingmomentscakes.com` | Literal |

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

#### Frontend URLs (used in email templates & CORS)

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `WEB_URL` | `https://meltingmomentscakes.com` | Literal |
| `ADMIN_URL` | `https://admin.meltingmomentscakes.com` | Literal |

#### Backups

| Secret | Production value | Notes |
|--------|-----------------|-------|
| `BACKUP_GCS_BUCKET` | `melting-moments-cakes-backups` | GCS bucket created in Step 8 |

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

**Database backups**: Cron runs `scripts/backup-db.sh` daily at 2 AM, uploads to GCS, retains 7 days locally.

**Scaling**: If the e2-micro becomes a bottleneck, upgrade in-place: `gcloud compute instances set-machine-type mm-backend --machine-type=e2-small --zone=me-central1-a` (requires VM stop/start).
