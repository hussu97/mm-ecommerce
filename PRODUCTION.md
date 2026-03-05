# Production Deployment Guide — Melting Moments Ecommerce

## Architecture

```
Internet
├── meltingmomentscakes.com        → Vercel (web storefront)
├── admin.meltingmomentscakes.com  → Vercel (admin panel)
├── api.meltingmomentscakes.com    → GCP VM: FastAPI via Nginx + SSL
├── analytics.meltingmomentscakes.com → GCP VM: Umami via Nginx + SSL
└── media.meltingmomentscakes.com  → Cloudflare R2 (object storage)
```

**GCP VM** (e2-small, 2 vCPU shared, 2 GB RAM) runs:
- PostgreSQL 16
- FastAPI (4 Uvicorn workers)
- Umami + Umami DB
- Nginx (reverse proxy for `api.*` and `analytics.*`)
- Certbot (SSL for those two subdomains only)

**Vercel** hosts both Next.js apps — free Hobby plan, global CDN, automatic deployments on push to `main`.

---

## Monthly Cost Estimate

| Service | Provider | Plan | Est. $/mo |
|---------|----------|------|-----------|
| Web storefront (Next.js) | Vercel | Hobby (free) | $0 |
| Admin panel (Next.js) | Vercel | Hobby (free, same account) | $0 |
| Backend VM (e2-small) | GCP Compute Engine | On-demand + sustained discount | ~$10 |
| Boot disk (30 GB SSD) | GCP Persistent Disk | Standard SSD | ~$5 |
| Database backups | GCP Cloud Storage | Standard, ~2 GB | ~$0.05 |
| Network egress | GCP | ~5 GB/mo | ~$0.40 |
| Media storage | Cloudflare R2 | Free tier (10 GB) | $0 |
| SSL certificates | Let's Encrypt | Free | $0 |
| **Total** | | | **~$15–16/mo** |

---

## Prerequisites

- [ ] GCP account with billing enabled
- [ ] Domain registered and pointing to Cloudflare (for DNS management)
- [ ] Cloudflare R2 bucket created (`mm-media`, public access enabled)
- [ ] Stripe account with live API keys + webhook configured
- [ ] Resend account with verified sending domain
- [ ] Vercel account (free Hobby plan is sufficient)
- [ ] GitHub repository with this codebase
- [ ] Local `gcloud` CLI installed (for initial setup)

---

## Step 1: GCP Project + Billing

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a new project: `mm-ecommerce`
2. Enable billing for the project
3. Enable the Compute Engine API:
   ```
   gcloud services enable compute.googleapis.com --project=mm-ecommerce
   ```

---

## Step 2: Create Compute Engine VM

```bash
gcloud compute instances create mm-backend \
  --project=mm-ecommerce \
  --zone=me-central1-a \
  --machine-type=e2-small \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
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
  --project=mm-ecommerce \
  --allow=tcp:80,tcp:443 \
  --target-tags=http-server,https-server \
  --description="Allow web traffic"

# SSH is already open by default (tcp:22)
```

---

## Step 4: SSH into VM, Install Dependencies

```bash
# SSH via gcloud (handles key management automatically)
gcloud compute ssh mm-backend --project=mm-ecommerce --zone=me-central1-a
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
gcloud init  # follow prompts; select project mm-ecommerce
```

---

## Step 5: Clone Repo + Configure Environment

```bash
sudo mkdir -p /opt/mm-ecommerce
sudo chown $USER:$USER /opt/mm-ecommerce

git clone https://github.com/your-org/mm-ecommerce.git /opt/mm-ecommerce
cd /opt/mm-ecommerce

cp .env.production.example .env
nano .env  # fill in all secrets (see template for field descriptions)
```

Key values to fill in `.env`:
- `POSTGRES_PASSWORD` — generate with `openssl rand -hex 20`
- `SECRET_KEY` — generate with `openssl rand -hex 32`
- `UMAMI_APP_SECRET` — generate with `openssl rand -hex 32`
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
- `RESEND_API_KEY`
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
- `BACKUP_GCS_BUCKET` — the GCS bucket name from Step 8

---

## Step 6: Launch Backend Services

```bash
cd /opt/mm-ecommerce

# Pull base images + build API
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml build api

# Start all backend services
docker compose -f docker-compose.prod.yml up -d

# Verify all containers are running
docker compose -f docker-compose.prod.yml ps

# Run migrations + seed initial data
docker compose -f docker-compose.prod.yml exec api alembic upgrade head
docker compose -f docker-compose.prod.yml exec api python -m scripts.seed_db
```

Expected running services: `postgres`, `umami-db`, `umami`, `api`, `nginx`, `certbot`

---

## Step 7: Issue SSL Certificates

Wait until DNS is pointing `api.*` and `analytics.*` to the VM IP (Step 12 first).

```bash
cd /opt/mm-ecommerce

# Issue cert for api subdomain
docker compose -f docker-compose.prod.yml run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d api.meltingmomentscakes.com

# Issue cert for analytics subdomain
docker compose -f docker-compose.prod.yml run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d analytics.meltingmomentscakes.com

# Reload nginx to pick up certs
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
```

Verify: `curl https://api.meltingmomentscakes.com/health` should return `{"status": "ok"}`.

---

## Step 8: GCP Cloud Storage Bucket + Service Account

```bash
# Create backup bucket (in the same region as the VM)
gcloud storage buckets create gs://mm-ecommerce-backups \
  --project=mm-ecommerce \
  --location=ME-CENTRAL1 \
  --uniform-bucket-level-access

# Create a service account for backups
gcloud iam service-accounts create mm-backup-sa \
  --project=mm-ecommerce \
  --display-name="MM Backup Service Account"

# Grant it permission to write to the bucket only
gcloud storage buckets add-iam-policy-binding gs://mm-ecommerce-backups \
  --member="serviceAccount:mm-backup-sa@mm-ecommerce.iam.gserviceaccount.com" \
  --role="roles/storage.objectCreator"

# Attach the service account to the VM
gcloud compute instances set-service-account mm-backend \
  --project=mm-ecommerce \
  --zone=me-central1-a \
  --service-account=mm-backup-sa@mm-ecommerce.iam.gserviceaccount.com \
  --scopes=https://www.googleapis.com/auth/devstorage.read_write
```

Then set `BACKUP_GCS_BUCKET=mm-ecommerce-backups` in `.env`.

---

## Step 9: Schedule Automated Backups (Cron)

```bash
# Make backup script executable
chmod +x /opt/mm-ecommerce/scripts/backup-db.sh

# Open crontab
crontab -e
```

Add this line (runs daily at 2 AM):
```
0 2 * * * DEPLOY_DIR=/opt/mm-ecommerce /opt/mm-ecommerce/scripts/backup-db.sh >> /var/log/mm-backup.log 2>&1
```

Test it manually:
```bash
DEPLOY_DIR=/opt/mm-ecommerce /opt/mm-ecommerce/scripts/backup-db.sh
# Verify file appears in GCS:
gsutil ls gs://mm-ecommerce-backups/backups/
```

---

## Step 10: Vercel — Web Storefront

1. Go to [vercel.com](https://vercel.com) and sign in with GitHub
2. Click **Add New Project** → import `mm-ecommerce`
3. Configure:
   - **Framework Preset**: Next.js
   - **Root Directory**: `apps/web`
   - **Build Command**: `cd ../.. && pnpm build --filter=web`
   - **Output Directory**: `.next`
4. Add **Environment Variables**:
   ```
   NEXT_PUBLIC_API_URL=https://api.meltingmomentscakes.com
   NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_live_CHANGE_ME
   NEXT_PUBLIC_UMAMI_SCRIPT_URL=https://analytics.meltingmomentscakes.com/script.js
   NEXT_PUBLIC_UMAMI_WEBSITE_ID=<from Umami dashboard after Step 6>
   ```
5. Click **Deploy** and note the preview URL (e.g. `mm-ecommerce-web.vercel.app`)
6. Once confirmed working, go to **Settings → Domains** → add `meltingmomentscakes.com`

---

## Step 11: Vercel — Admin Panel

1. In the same Vercel account, click **Add New Project** → import `mm-ecommerce` again
2. Configure:
   - **Framework Preset**: Next.js
   - **Root Directory**: `apps/admin`
   - **Build Command**: `cd ../.. && pnpm build --filter=admin`
   - **Output Directory**: `.next`
3. Add **Environment Variables**:
   ```
   NEXT_PUBLIC_API_URL=https://api.meltingmomentscakes.com
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
| A | `analytics` | GCP VM external IP | Umami |
| CNAME | `media` | `<your-r2-bucket>.r2.cloudflarestorage.com` | R2 media |

> **Vercel custom domains**: After adding a custom domain in Vercel, it will show you the exact DNS record needed (either A or CNAME depending on apex vs subdomain). Follow those instructions; the values above are typical.

---

## Step 13: GitHub Actions Secrets

In GitHub → repo → **Settings → Secrets and variables → Actions → Environments → production**, add:

| Secret | Value |
|--------|-------|
| `SERVER_HOST` | GCP VM external IP address |
| `SERVER_USER` | Your VM username (e.g. `debian` or your username) |
| `SERVER_SSH_KEY` | Private SSH key that can access the VM (generate with `ssh-keygen`, add public key to VM's `~/.ssh/authorized_keys`) |

The `deploy.yml` workflow will then SSH into the GCP VM and run `scripts/deploy.sh` automatically on every push to `main`. Vercel handles web + admin deployments automatically via its GitHub integration.

---

## Step 14: Post-Deployment Smoke Test

Run these checks after completing all steps:

```bash
# API health check
curl https://api.meltingmomentscakes.com/health
# Expected: {"status": "ok"}

# API docs accessible
curl -s https://api.meltingmomentscakes.com/docs | grep -c "swagger"

# Umami analytics running
curl -s -o /dev/null -w "%{http_code}" https://analytics.meltingmomentscakes.com
# Expected: 200

# All backend containers healthy
ssh <VM> "docker compose -f /opt/mm-ecommerce/docker-compose.prod.yml ps"
# All services should show: Up (healthy) or Up

# Vercel deployments
# Visit https://meltingmomentscakes.com — storefront loads
# Visit https://admin.meltingmomentscakes.com — admin login loads

# Backup script
ssh <VM> "DEPLOY_DIR=/opt/mm-ecommerce /opt/mm-ecommerce/scripts/backup-db.sh"
# Should print "Backup complete" and upload to GCS

# SSL certificates valid
echo | openssl s_client -connect api.meltingmomentscakes.com:443 2>/dev/null | openssl x509 -noout -dates
echo | openssl s_client -connect analytics.meltingmomentscakes.com:443 2>/dev/null | openssl x509 -noout -dates
```

---

## Ongoing Maintenance

**Deployments**: Push to `main` → GitHub Actions SSHes into GCP VM and runs `scripts/deploy.sh` (API only). Vercel auto-deploys web + admin.

**SSL renewal**: Handled automatically by the `certbot` container (runs every 12 hours).

**Database backups**: Cron runs `scripts/backup-db.sh` daily at 2 AM, uploads to GCS, retains 7 days locally.

**Scaling**: If the e2-small becomes a bottleneck, upgrade in-place: `gcloud compute instances set-machine-type mm-backend --machine-type=e2-medium --zone=me-central1-a` (requires VM stop/start).
