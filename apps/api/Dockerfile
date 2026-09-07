# ── Stage 1: builder ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Suppress debconf "unable to initialize frontend" warnings during apt-get
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project definition — changes here bust only the pip layer, not apt.
# uv.lock travels with pyproject.toml: F-OPS-17 below resolves from it, not
# from a fresh solve, so this is the file that has to be here for that to work.
COPY apps/api/pyproject.toml apps/api/setup.cfg* apps/api/uv.lock ./

# Use BuildKit pip cache so downloaded wheels survive layer-cache misses.
# On a cold layer cache this turns a 19-minute download into ~2 minutes.
#
# F-OPS-17: every dependency in pyproject.toml is a bare `>=`, so a plain
# `pip install .` re-resolves the whole tree fresh on every build — a
# transitive package can ship a breaking release between one deploy and the
# next with no code change here to review. `uv.lock` pins exact versions;
# `uv export --locked` reads it as the single source of truth and FAILS the
# build if it is out of sync with pyproject.toml (a dependency edited without
# a follow-up `uv lock`), rather than silently resolving around the mismatch.
# `--no-emit-project` + a separate `--no-deps .` afterwards keeps the same
# two-step shape as before: this source-less builder stage only ever
# produces an install of the DEPENDENCIES (no `app/`/`scripts/` are present
# here to package — see the runner stage's own `COPY` for those), and the
# `--no-deps .` after it is a no-op that preserves that behaviour exactly.
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/uv \
    pip install --upgrade pip uv && \
    uv export --locked --no-dev --no-emit-project --no-hashes \
      -o requirements.lock.txt && \
    pip install --prefix=/install -r requirements.lock.txt && \
    pip install --prefix=/install --no-deps .

# ── Stage 2: runner ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runner

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Runtime system deps only (curl needed for Docker healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd -r api && useradd -r -g api -d /app -s /sbin/nologin api

# Copy application source (chown avoids a separate RUN layer)
COPY --chown=api:api apps/api/ .

USER api

EXPOSE 8000

# Migrations are NOT run automatically — run them manually before deploying:
#   docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1} --log-level warning --timeout-graceful-shutdown 8"]
