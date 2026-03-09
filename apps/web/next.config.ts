import type { NextConfig } from "next";

// Build locale pattern from the same env var the middleware uses (e.g. "en|ar")
const localePattern = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? "en,ar")
  .split(",")
  .map((l) => l.trim())
  .join("|");

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
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
  webpack: (config, { dev }) => {
    if (dev) {
      config.cache = false;
    }
    return config;
  },
  images: {
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

export default nextConfig;
