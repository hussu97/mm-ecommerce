import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // output: "standalone" is for self-hosted Docker only — not needed on Vercel
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

export default nextConfig;
