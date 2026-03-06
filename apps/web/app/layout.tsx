import type { Metadata } from "next";
import { Raleway, Jost, Tajawal, Cairo } from "next/font/google";
import Script from "next/script";
import { cookies } from "next/headers";
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

const tajawal = Tajawal({
  subsets: ["arabic"],
  weight: ["300", "400", "500", "700"],
  variable: "--font-tajawal",
  display: "swap",
});

const cairo = Cairo({
  subsets: ["arabic"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-cairo",
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
  const cookieStore = await cookies();
  const locale = cookieStore.get('mm_locale')?.value ?? 'en';
  const dir = locale === 'ar' ? 'rtl' : 'ltr';

  return (
    <html lang={locale} dir={dir} className={`${raleway.variable} ${jost.variable} ${tajawal.variable} ${cairo.variable}`}>
      <head>
        <link
          href="https://fonts.googleapis.com/icon?family=Material+Icons"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen flex flex-col antialiased">
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var m=localStorage.getItem('mm_theme');if(m==='dark')document.documentElement.classList.add('dark');var l=document.cookie.match(/mm_locale=([^;]+)/);if(l&&l[1]){var langs={ar:'rtl'};if(langs[l[1]])document.documentElement.setAttribute('dir',langs[l[1]]);document.documentElement.setAttribute('lang',l[1])}}catch(e){}})()`,
          }}
        />
        <Providers>
          {children}
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
