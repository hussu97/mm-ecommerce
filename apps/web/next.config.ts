import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

// Build locale pattern from the same env var the middleware uses (e.g. "en|ar")
const localePattern = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? "en,ar")
  .split(",")
  .map((l) => l.trim())
  .join("|");

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
      {
        source: "/umami/script.js",
        destination: "https://cloud.umami.is/script.js",
      },
      {
        source: "/umami/api/send",
        destination: "https://cloud.umami.is/api/send",
      },
      // Next.js i18n middleware prefixes paths with the active locale
      // (e.g. /en/umami/api/send). Add explicit locale-prefixed rewrites so
      // the proxy still works regardless of which locale is active.
      {
        source: `/:locale(${localePattern})/umami/script.js`,
        destination: "https://cloud.umami.is/script.js",
      },
      {
        source: `/:locale(${localePattern})/umami/api/send`,
        destination: "https://cloud.umami.is/api/send",
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
    // Product/category images are already CDN-hosted; avoid burning Vercel's
    // limited Image Optimization transformations on every storefront variant.
    unoptimized: true,
    deviceSizes: [640, 750, 828, 1080, 1200, 1920],
    imageSizes: [16, 32, 48, 64, 96, 128, 256, 384],
    formats: ["image/avif", "image/webp"],
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
