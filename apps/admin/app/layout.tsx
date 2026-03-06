import type { Metadata } from "next";
import { Raleway, Jost } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

const raleway = Raleway({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-raleway",
  display: "swap",
});

const jost = Jost({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  variable: "--font-jost",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Admin — Melting Moments Cakes",
    template: "%s | MM Admin",
  },
  description: "Admin dashboard for Melting Moments Cakes",
  robots: "noindex, nofollow",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${raleway.variable} ${jost.variable}`}>
      <head>
        <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet" />
      </head>
      <body className="min-h-screen antialiased bg-gray-50">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
