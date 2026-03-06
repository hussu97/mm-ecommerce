#!/usr/bin/env bash
# First-time GCP VM setup for Melting Moments Ecommerce backend.
# Deploys: PostgreSQL, FastAPI, Umami, Nginx, Certbot.
# Note: web + admin (Next.js) are deployed separately on Vercel.
# Run as root or a user with sudo + docker access.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/your-org/melting-moments-cakes.git}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/melting-moments-cakes}"

echo "==> Cloning repository..."
if [ -d "$DEPLOY_DIR" ]; then
  echo "    Directory $DEPLOY_DIR already exists, skipping clone."
else
  git clone "$REPO_URL" "$DEPLOY_DIR"
fi

cd "$DEPLOY_DIR"

echo "==> Setting up environment file..."
if [ ! -f .env ]; then
  if [ -f .env.production.example ]; then
    cp .env.production.example .env
    echo "    Copied .env.production.example → .env"
    echo "    !! Edit .env and fill in all secrets before continuing !!"
    echo "    Run: nano $DEPLOY_DIR/.env"
    exit 1
  else
    echo "    ERROR: .env.production.example not found."
    exit 1
  fi
else
  echo "    .env already exists, skipping."
fi

echo "==> Pulling Docker images..."
docker compose -f docker-compose.prod.yml pull

echo "==> Building API image..."
docker compose -f docker-compose.prod.yml build api

echo "==> Starting backend services..."
docker compose -f docker-compose.prod.yml up -d

echo "==> Waiting for database to be ready..."
sleep 15

echo "==> Running database migrations..."
docker compose -f docker-compose.prod.yml exec -T api alembic upgrade head

echo "==> Seeding initial data (admin user, categories)..."
docker compose -f docker-compose.prod.yml exec -T api python -m scripts.seed_db

echo ""
echo "==> Setup complete!"
echo "    Services running:"
docker compose -f docker-compose.prod.yml ps
echo ""
echo "    Next steps:"
echo "    1. Point DNS A records for api.* and analytics.* to this VM's external IP"
echo "    2. Obtain SSL certificates (api + analytics subdomains only):"
echo "       docker compose -f docker-compose.prod.yml run --rm certbot certonly \\"
echo "         --webroot -w /var/www/certbot \\"
echo "         -d api.meltingmomentscakes.com \\"
echo "         -d analytics.meltingmomentscakes.com"
echo "    3. Reload nginx: docker compose -f docker-compose.prod.yml exec nginx nginx -s reload"
echo "    4. Deploy web + admin on Vercel (see PRODUCTION.md Steps 10-11)"
