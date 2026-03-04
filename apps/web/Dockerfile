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

RUN pnpm install --frozen-lockfile

# ── Stage 2: builder ───────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

RUN npm install -g pnpm@9.15.0

WORKDIR /repo

COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/web/node_modules ./apps/web/node_modules
COPY --from=deps /repo/packages/ui/node_modules ./packages/ui/node_modules 2>/dev/null || true
COPY --from=deps /repo/packages/types/node_modules ./packages/types/node_modules 2>/dev/null || true
COPY --from=deps /repo/packages/config/node_modules ./packages/config/node_modules 2>/dev/null || true

COPY . .

# Build only the web app
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter web build

# ── Stage 3: runner ────────────────────────────────────────────────────────────
FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup -S nodejs && adduser -S nextjs -G nodejs

# Copy standalone output (Next.js standalone preserves monorepo path structure)
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/public ./apps/web/public

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "apps/web/server.js"]
