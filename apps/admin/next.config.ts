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
          { key: "X-Content-Type-Options", value: "nosniff" },
          // The console is never a referrer to anywhere: its URLs carry order
          // numbers and customer ids in the path.
          { key: "Referrer-Policy", value: "no-referrer" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), payment=(), usb=(), geolocation=()",
          },
          {
            key: "Strict-Transport-Security",
            value: "max-age=63072000; includeSubDomains; preload",
          },
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          { key: "X-Frame-Options", value: "DENY" },
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
    // Every host a `products.image_urls` / `categories.image_url` value has ever
    // pointed at. A host missing here is not a broken image on a page nobody
    // notices — next/image answers 400 for it, so the admin shows a placeholder
    // for the whole catalogue while the storefront (which runs `unoptimized`)
    // looks fine, and it reads as a storage-permission fault rather than a
    // config one. Add the host here whenever the media origin moves.
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
        // Where the live catalogue's images actually are today: the Foodics
        // menu import copied them into this bucket.
        protocol: "https",
        hostname: "storage.googleapis.com",
      },
      {
        // CLOUDFLARE_R2_PUBLIC_URL in production — what new uploads return.
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
