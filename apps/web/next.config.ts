import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  // output: "standalone" is for self-hosted Docker only — not needed on Vercel
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
        headers: [{ key: 'X-Robots-Tag', value: 'all' }],
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
