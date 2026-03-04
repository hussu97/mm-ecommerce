#!/usr/bin/env bash
# First-time VPS setup for Melting Moments Ecommerce.
# Run as root or a user with sudo + docker access.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/your-org/mm-ecommerce.git}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/mm-ecommerce}"

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

echo "==> Building application images..."
docker compose -f docker-compose.prod.yml build

echo "==> Starting all services..."
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
echo "    1. Point DNS for your domains to this server's IP"
echo "    2. Obtain SSL certificates:"
echo "       docker compose -f docker-compose.prod.yml exec certbot certbot certonly \\"
echo "         --webroot -w /var/www/certbot -d meltingmomentscakes.com -d www.meltingmomentscakes.com"
echo "    3. Reload nginx: docker compose -f docker-compose.prod.yml exec nginx nginx -s reload"
