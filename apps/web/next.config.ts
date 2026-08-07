import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  // output: "standalone" is for self-hosted Docker only — not needed on Vercel
  experimental: {
    // How long the client router may reuse a prefetched entry before asking for
    // it again. The default for a dynamic route is 0, which means a prefetch is
    // stale the moment it lands: a link scrolling back into view, or a re-render
    // of the nav, refetches the whole route. That is why one homepage view was
    // measured making 29 prefetch requests for 15 distinct routes — each route
    // fetched roughly twice, for nothing.
    //
    // 30s is well inside the window in which somebody actually clicks the link
    // they were just shown, and every one of these routes is a catalogue page
    // whose contents do not change minute to minute.
    staleTimes: {
      dynamic: 30,
      static: 180,
    },
  },
  async rewrites() {
    return [
      // Proxy API requests through Next.js so auth cookies are same-origin.
      // Only active when NEXT_PUBLIC_API_URL is a relative path (dev mode).
      // In production the env var is an absolute HTTPS URL, so the rewrite
      // never triggers and the browser calls the API domain directly.
      {
        source: "/api/v1/:path*",
        destination: `${process.env.NEXT_PRIVATE_API_HOST ?? "http://localhost:8000"}/api/v1/:path*`,
      },
      // Analytics, served from this origin so blocklists have nothing to match.
      // The nondescript prefix and filename are deliberate — see
      // `app/vague/api/send/route.ts`. Both `/umami/*` paths this replaced were
      // clear of every list checked; the rename is insurance, not a repair.
      //
      // The companion `/vague/api/send` is deliberately absent here: it is a route
      // handler, because a rewrite opens its own connection to Umami and the
      // visitor's location is lost with it.
      {
        source: "/vague/v.js",
        destination: "https://cloud.umami.is/script.js",
      },
    ];
  },
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Robots-Tag', value: 'all' },
          // The API host gets these from nginx; the Vercel-hosted apps got
          // nothing at all, so the storefront was the one surface with no
          // security headers on it.
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          {
            key: 'Permissions-Policy',
            // Geolocation stays: the checkout map offers "use my location".
            value: 'camera=(), microphone=(), payment=(), usb=(), geolocation=(self)',
          },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=63072000; includeSubDomains; preload',
          },
          // Clickjacking cover. `frame-ancestors` is the modern control and
          // X-Frame-Options the fallback for anything that predates it.
          { key: 'Content-Security-Policy', value: "frame-ancestors 'none'" },
          { key: 'X-Frame-Options', value: 'DENY' },
          // The full policy, reported and not enforced.
          //
          // Deliberately not enforced yet: a wrong directive here is a blank
          // storefront, and this list is derived from reading the source rather
          // than from watching a real session. Load the site, check the console
          // for violations, fix the policy, and only then move this to
          // `Content-Security-Policy`.
          //
          // `unsafe-inline` on scripts is required while Next's hydration
          // bootstrap is inline and unnonced; moving to a nonce needs the
          // middleware to generate one per request.
          {
            key: 'Content-Security-Policy-Report-Only',
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com https://maps.googleapis.com",
              "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
              "font-src 'self' https://fonts.gstatic.com data:",
              "img-src 'self' data: blob: https://*.r2.cloudflarestorage.com https://pub-*.r2.dev https://storage.googleapis.com https://media.meltingmomentscakes.com https://maps.googleapis.com https://maps.gstatic.com https://*.googleusercontent.com",
              "connect-src 'self' https://api.meltingmomentscakes.com https://cloud.umami.is https://maps.googleapis.com https://*.ingest.sentry.io https://*.ingest.de.sentry.io",
              "frame-src https://challenges.cloudflare.com",
              "frame-ancestors 'none'",
              "base-uri 'self'",
              "form-action 'self'",
              "object-src 'none'",
              'upgrade-insecure-requests',
            ].join('; '),
          },
        ],
      },
    ];
  },
  images: {
    // Optimization is billed and cached per unique (image, width, quality), and
    // the first request for a combination nobody has asked for yet pays for the
    // encode — a cold transform measured 3.1s TTFB when the sources were 2048px
    // /350KB JPEGs. Those sources are now capped at 1400px, which is the lever
    // that actually moves this: less to fetch from GCS and less to decode.
    //
    // These two lists are already trimmed against Next's defaults (no 2048/3840)
    // and stay as they are — `sizes` on the product grids resolves to ~960 CSS px
    // at 2x, and the full-bleed about-page shots are what keeps 1920 in.
    deviceSizes: [640, 750, 828, 1080, 1200, 1920],
    imageSizes: [16, 32, 48, 64, 96, 128, 256, 384],
    formats: ["image/avif", "image/webp"],
    // Every `next/image` on the site renders at the default quality. Declaring
    // the allowed set means a hand-edited or crawled `?q=` cannot mint a second
    // full set of transforms for the whole catalogue.
    qualities: [75],
    remotePatterns: [
      {
        protocol: "https",
        hostname: "**.r2.cloudflarestorage.com",
      },
      {
        protocol: "https",
        hostname: "pub-**.r2.dev",
      },
      {
        protocol: "https",
        hostname: "storage.googleapis.com",
      },
      {
        protocol: "https",
        hostname: "media.meltingmomentscakes.com",
      },
      {
        protocol: "https",
        hostname: "foodics-console-production.s3.eu-west-1.amazonaws.com",
      },
    ],
  },
};

const sentryEnabled = Boolean(
  process.env.NEXT_PUBLIC_SENTRY_DSN ?? process.env.SENTRY_DSN ?? process.env.SENTRY_AUTH_TOKEN,
);

const exportedConfig = sentryEnabled
  ? withSentryConfig(nextConfig, {
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT ?? "mm-frontend",
      authToken: process.env.SENTRY_AUTH_TOKEN,
      silent: !process.env.CI,
      tunnelRoute: "/monitoring",
      widenClientFileUpload: true,
      webpack: {
        treeshake: {
          removeDebugLogging: true,
        },
      },
      sourcemaps: {
        disable: !process.env.SENTRY_AUTH_TOKEN,
      },
    })
  : nextConfig;

export default exportedConfig;
