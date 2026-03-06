import type { Metadata } from "next";
import { Playfair_Display, Jost } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import { Providers } from "./providers";
import { Header } from "@/components/layout/Header";
import { CategoryNavLinks } from "@/components/layout/CategoryNav";
import { Footer } from "@/components/layout/Footer";
import { PromoBanner } from "@/components/layout/PromoBanner";
import type { Category } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

async function getActiveCategories(): Promise<Category[]> {
  try {
    const res = await fetch(`${API_BASE}/categories`, { next: { revalidate: 300 } });
    if (!res.ok) return [];
    const data: Category[] = await res.json();
    return data.filter((c) => c.is_active).sort((a, b) => a.display_order - b.display_order);
  } catch {
    return [];
  }
}

const playfairDisplay = Playfair_Display({
  subsets: ["latin"],
  weight: ["400", "600"],
  style: ["normal", "italic"],
  variable: "--font-playfair",
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
    default: "Melting Moments Cakes - Artisanal Bakery in UAE",
    template: "%s | Melting Moments Cakes",
  },
  description:
    "Handcrafted brownies, cookies, cookie melts, and desserts delivered across the UAE. Made with 100% love by Fatema Abbasi.",
  keywords: ["brownies", "cookies", "bakery", "UAE", "Dubai", "Sharjah", "artisanal"],
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://meltingmomentscakes.com"),
  openGraph: {
    siteName: "Melting Moments Cakes",
    locale: "en_AE",
    type: "website",
    images: [{ url: "/images/logos/color_logo.jpeg", width: 800, height: 800, alt: "Melting Moments Cakes" }],
  },
  twitter: {
    card: "summary_large_image",
    images: ["/images/logos/color_logo.jpeg"],
  },
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const categories = await getActiveCategories();

  return (
    <html lang="en" className={`${playfairDisplay.variable} ${jost.variable}`}>
      <head>
        <link
          href="https://fonts.googleapis.com/icon?family=Material+Icons"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen flex flex-col antialiased">
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var m=localStorage.getItem('mm_theme');if(m==='dark')document.documentElement.classList.add('dark')}catch(e){}})()`,
          }}
        />
        <Providers>
          <PromoBanner />
          <Header />
          <CategoryNavLinks categories={categories} />
          <main className="flex-1">
            {children}
          </main>
          <Footer />
        </Providers>

        {/* Umami analytics — no-cookie, GDPR-friendly */}
        {process.env.NEXT_PUBLIC_UMAMI_WEBSITE_ID && (
          <Script
            src={process.env.NEXT_PUBLIC_UMAMI_URL ?? 'https://cloud.umami.is/script.js'}
            data-website-id={process.env.NEXT_PUBLIC_UMAMI_WEBSITE_ID}
            strategy="afterInteractive"
          />
        )}
      </body>
    </html>
  );
}
