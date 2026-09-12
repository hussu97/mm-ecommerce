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
  icons: {
    icon: "/favicon.ico",
    shortcut: "/favicon.ico",
  },
  robots: {
    index: false,
    follow: false,
    nocache: true,
    googleBot: {
      index: false,
      follow: false,
      noimageindex: true,
      "max-snippet": -1,
      "max-image-preview": "none",
      "max-video-preview": -1,
    },
  },
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
        {/* Set the saved density on <html> before first paint, so a compact
            user never sees a comfortable flash. Kept in step with
            `densityFromStorage()` in lib/density-context.tsx. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{document.documentElement.dataset.density=localStorage.getItem('mm-admin-density')==='compact'?'compact':'comfortable'}catch(e){}",
          }}
        />
      </head>
      <body className="min-h-screen antialiased bg-gray-50">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
