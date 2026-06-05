import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  // output: "standalone" is for self-hosted Docker only — not needed on Vercel
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "X-Robots-Tag",
            value: "noindex, nofollow, noarchive, nosnippet, noimageindex",
          },
        ],
      },
    ];
  },
  async rewrites() {
    return [
      // Proxy API requests through Next.js so auth cookies are same-origin (dev).
      {
        source: "/api/v1/:path*",
        destination: `${process.env.NEXT_PRIVATE_API_HOST ?? "http://localhost:8000"}/api/v1/:path*`,
      },
    ];
  },
  images: {
    deviceSizes: [640, 750, 828, 1080, 1200, 1920],
    imageSizes: [16, 32, 48, 64, 96, 128, 256, 384],
    formats: ["image/webp"],
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
      project: process.env.SENTRY_PROJECT ?? "mm-admin",
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
