import type { Metadata } from "next";
import { SpeedInsights } from "@vercel/speed-insights/next";
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
    default: "Melting Moments Cakes — Brownie & Dessert Delivery Across the UAE",
    template: "%s | Melting Moments Cakes",
  },
  description:
    "Home bakery in Sharjah delivering fudgy brownies, gooey cookies, cookie melts, cakes and desserts to Dubai, Sharjah, Ajman and the rest of the UAE. Baked to order by Fatema Abbasi.",
  keywords: [
    "brownie delivery Dubai", "dessert delivery Dubai", "dessert delivery Sharjah",
    "cookie delivery UAE", "cookie melts Dubai", "brownies Sharjah",
    "home bakery Sharjah", "home bakery Dubai", "birthday cake delivery Dubai",
    "cake delivery Sharjah", "eggless brownies UAE", "eggless cake Dubai",
    "dessert boxes Dubai", "corporate gifting Dubai", "Eid sweets UAE",
    "Ramadan dessert boxes", "halal bakery UAE", "same day dessert delivery Dubai",
    "Melting Moments Cakes", "Fatema Abbasi baker",
  ],
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://meltingmomentscakes.com"),
  openGraph: {
    siteName: "Melting Moments Cakes",
    locale: "en_AE",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
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
          rel="preload"
          href="https://fonts.googleapis.com/icon?family=Material+Icons"
          as="style"
        />
        <link
          rel="search"
          type="application/opensearchdescription+xml"
          title="Melting Moments Cakes"
          href="/opensearch.xml"
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
        <SpeedInsights />

        {/* Material Icons — loaded async to avoid render-blocking */}
        <Script id="material-icons" strategy="afterInteractive">{`(function(){var l=document.createElement('link');l.rel='stylesheet';l.href='https://fonts.googleapis.com/icon?family=Material+Icons';document.head.appendChild(l)})()`}</Script>

        {/* Umami analytics — no-cookie, GDPR-friendly.
            Both paths are first-party and name neither the tool nor the shop, so
            there is no product name for a blocklist to match; the tracker
            appends `/api/send` to `data-host-url` itself. See
            `app/vague/api/send/route.ts`.

            Neither path is configurable, and `NEXT_PUBLIC_UMAMI_URL` is
            deliberately not read here any more. Both are internal to this app —
            the script is a rewrite in `next.config.ts`, the send is a route
            handler — so an environment that disagrees with the code is not a
            deployment choice, it is a fault. It was one: renaming the paths on
            6 August 2026 left a stale `/umami/script.js` in the Vercel
            environment, the tag pointed at a 404 and the tracker stopped
            loading entirely, while `data-host-url` moved with the code and
            nothing looked wrong. Only the website ID belongs in the
            environment. */}
        {process.env.NEXT_PUBLIC_UMAMI_WEBSITE_ID && (
          <Script
            src="/vague/v.js"
            data-website-id={process.env.NEXT_PUBLIC_UMAMI_WEBSITE_ID}
            data-host-url="/vague"
            strategy="afterInteractive"
          />
        )}
      </body>
    </html>
  );
}
