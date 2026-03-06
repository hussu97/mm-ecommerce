# ── Stage 1: builder ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project definition and install deps into a local prefix
COPY apps/api/pyproject.toml apps/api/setup.cfg* ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install . --no-deps || true

# Install all runtime deps into /install
RUN pip install --prefix=/install .

# ── Stage 2: runner ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runner

WORKDIR /app

# Runtime system deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd -r api && useradd -r -g api -d /app -s /sbin/nologin api

# Copy application source
COPY apps/api/ .

# Own app directory
RUN chown -R api:api /app

USER api

EXPOSE 8000

# Migrations are NOT run automatically — run them manually before deploying:
#   docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}"]
