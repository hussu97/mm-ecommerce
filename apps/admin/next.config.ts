import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  // output: "standalone" is for self-hosted Docker only — not needed on Vercel
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

const exportedConfig = process.env.NEXT_PUBLIC_SENTRY_DSN
  ? withSentryConfig(nextConfig, { silent: true })
  : nextConfig;

export default exportedConfig;
