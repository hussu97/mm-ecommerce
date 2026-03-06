# ── Stage 1: deps ──────────────────────────────────────────────────────────────
FROM node:20-alpine AS deps

RUN npm install -g pnpm@9.15.0

WORKDIR /repo

# Copy package manifests for layer caching
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json ./apps/web/
COPY apps/admin/package.json ./apps/admin/
COPY packages/ui/package.json ./packages/ui/
COPY packages/types/package.json ./packages/types/
COPY packages/config/package.json ./packages/config/

RUN pnpm install --frozen-lockfile && \
    mkdir -p packages/ui/node_modules packages/types/node_modules packages/config/node_modules

# ── Stage 2: builder ───────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

RUN npm install -g pnpm@9.15.0

WORKDIR /repo

COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/admin/node_modules ./apps/admin/node_modules
COPY --from=deps /repo/packages/ui/node_modules ./packages/ui/node_modules
COPY --from=deps /repo/packages/types/node_modules ./packages/types/node_modules
COPY --from=deps /repo/packages/config/node_modules ./packages/config/node_modules

COPY . .

# Build only the admin app
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter admin build

# ── Stage 3: runner ────────────────────────────────────────────────────────────
FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup -S nodejs && adduser -S nextjs -G nodejs

# Copy standalone output
COPY --from=builder --chown=nextjs:nodejs /repo/apps/admin/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /repo/apps/admin/.next/static ./apps/admin/.next/static
COPY --from=builder --chown=nextjs:nodejs /repo/apps/admin/public ./apps/admin/public

USER nextjs

EXPOSE 3001

ENV PORT=3001
ENV HOSTNAME="0.0.0.0"

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget -qO- http://localhost:3001/ 2>/dev/null | head -c1 || exit 1

CMD ["node", "apps/admin/server.js"]
