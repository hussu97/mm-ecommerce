import type { Metadata } from "next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import { Raleway, Jost, Tajawal, Cairo } from "next/font/google";
import Script from "next/script";
import { notFound } from "next/navigation";

import "../globals.css";
import { Providers } from "../providers";
import { TranslationProvider } from "@/lib/i18n/TranslationProvider";
import { getTranslations, getLanguages, createT } from "@/lib/i18n/server";
import { getActiveCategories } from "@/lib/catalogue";
import { Header } from "@/components/layout/Header";
import { CategoryNavLinks } from "@/components/layout/CategoryNav";
import { Footer } from "@/components/layout/Footer";
import type { Language } from "@/lib/types";

/**
 * This is the application's root layout, and it lives under `[locale]` on
 * purpose.
 *
 * It used to be split in two: an `app/layout.tsx` that owned `<html>` and read
 * the locale out of a cookie, and this file, which owned the chrome. Reading
 * `cookies()` in a *root* layout opts the entire tree out of static rendering —
 * every one of the 27 page routes built as dynamic, every response went out
 * `no-store`, and the CDN never held a thing. It was reading something the URL
 * already knew: the locale is right there in `params`.
 *
 * Every page lives under `/[locale]`, and the proxy in `proxy.ts` sends anything
 * unprefixed to a language before it reaches the router, so there is no page
 * outside this segment for the old root layout to have served. What is left
 * outside it — `sitemap.ts`, `robots.ts`, the `llms.txt` handlers — are route
 * handlers and metadata files, which never had a layout to begin with.
 */

const SUPPORTED_LOCALES = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? "en,ar").split(",");

/**
 * Four families, none of them preloaded, and no 300 weight anywhere.
 *
 * `preload` was on by default, which meant `next/font` emitted a preload for
 * every family on every page — 132 KB across 7 files, of which roughly half was
 * always the wrong alphabet. An English page eagerly fetched 64 KB of Tajawal
 * and Cairo; an Arabic page fetched 68 KB of Raleway and Jost. On a phone that
 * is bandwidth taken directly from the LCP image.
 *
 * Turning it off is what makes the split work, because `globals.css` already
 * picks by script: `body` resolves to Jost, `[dir="rtl"] body` to Cairo. Left
 * to discover the faces through the stylesheet, a browser downloads only the
 * two the page actually renders in. The cost is that discovery waits on the CSS
 * — which `display: "swap"` already covers, since text paints in the fallback
 * either way and never blocks on a font.
 *
 * The 300s are gone because nothing asked for them. The only weight utilities
 * in the codebase are `font-medium` (500), `font-semibold` (600),
 * `font-normal` (400) and one `font-bold` (700); `font-light` appears nowhere,
 * so those four faces were downloaded and never drawn with.
 */

const raleway = Raleway({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-raleway",
  display: "swap",
  preload: false,
});

const jost = Jost({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-jost",
  display: "swap",
  preload: false,
});

const tajawal = Tajawal({
  subsets: ["arabic"],
  weight: ["400", "500", "700"],
  variable: "--font-tajawal",
  display: "swap",
  preload: false,
});

const cairo = Cairo({
  subsets: ["arabic"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-cairo",
  display: "swap",
  preload: false,
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
    "bakery Sharjah", "bakery Dubai", "birthday cake delivery Dubai",
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

export function generateStaticParams() {
  return SUPPORTED_LOCALES.map((locale) => ({ locale }));
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;

  if (!SUPPORTED_LOCALES.includes(locale)) {
    notFound();
  }

  const [categories, translations, languages] = await Promise.all([
    getActiveCategories(),
    getTranslations(locale),
    getLanguages(),
  ]);

  const currentLang: Language | undefined = languages.find((l) => l.code === locale);
  const direction = (currentLang?.direction ?? (locale === "ar" ? "rtl" : "ltr")) as "ltr" | "rtl";
  const t = createT(translations);

  // All four regardless of script, and it costs nothing to do so: these class
  // names only define `--font-raleway` and friends, and nothing reads them.
  // `globals.css` picks its faces by real family name ("Raleway", "Tajawal"),
  // and Next 16 emits `@font-face` under exactly those names, so the mapping is
  // redundant — which is also why dropping the Arabic pair here would not have
  // stopped them loading. What stops that is `preload: false` above.
  const fontVariables = `${raleway.variable} ${jost.variable} ${tajawal.variable} ${cairo.variable}`;

  return (
    <html lang={locale} dir={direction} className={fontVariables}>
      <head>
        <link
          rel="search"
          type="application/opensearchdescription+xml"
          title="Melting Moments Cakes"
          href="/opensearch.xml"
        />
      </head>
      <body className="min-h-screen flex flex-col antialiased">
        {/* Dark mode, applied before first paint. `lang` and `dir` are no longer
            patched here: they are rendered correctly by the server now that the
            locale comes from the URL rather than a cookie this could not see. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{if(localStorage.getItem('mm_theme')==='dark')document.documentElement.classList.add('dark')}catch(e){}})()`,
          }}
        />
        <Providers>
          <TranslationProvider locale={locale} direction={direction} translations={translations}>
            <Header languages={languages} categories={categories} locale={locale} />
            <CategoryNavLinks
              categories={categories}
              locale={locale}
              allLabel={t('nav.all')}
              allHref={`/${locale}/all-products`}
            />
            <main className="flex-1">
              {children}
            </main>
            <Footer />
          </TranslationProvider>
        </Providers>
        <SpeedInsights />

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

        {/* Microsoft Clarity — session recordings and heatmaps.
            Umami counts; this is the half that shows you what the count was made
            of. Every storefront event is mirrored into it as a filterable event
            from `lib/analytics.ts`, so a recording can be found by what happened
            in it rather than scrolled for. See `docs/microsoft-clarity-setup.md`.

            Loaded from clarity.ms rather than proxied through this origin, which
            is the one place this deliberately differs from Umami above. The tag
            hard-codes its own ingest hosts, so a first-party rewrite would serve
            the file and have it beacon to clarity.ms regardless — the rewrite
            would buy nothing and hide that it had bought nothing. A blocked
            visitor is simply not recorded, which is why Umami stays the source
            of truth for counts.

            Injected as Microsoft's own inline bootstrap rather than pointed at
            the tag with `src`, and that is not a style choice. The file at
            `clarity.ms/tag/<id>` is a 712-byte loader whose *first statement*
            calls `window.clarity(...)` and pushes onto `window.clarity.q` — it
            consumes the global, it does not define it. The stub below is what
            defines it. Loaded with `src` alone the tag threw
            `a[c] is not a function` on its first line, never fetched the real
            recorder from `scripts.clarity.ms`, and left `window.clarity`
            undefined forever — so every event mirrored from `lib/analytics.ts`
            was dropped while Umami looked perfectly healthy. Verified live on
            2026-08-10: script tag present, resource HTTP 200, global undefined.

            The stub also *is* the queue, which is the second reason it matters:
            calls made before the recorder finishes loading are buffered rather
            than lost. See `docs/microsoft-clarity-setup.md`. */}
        {process.env.NEXT_PUBLIC_CLARITY_PROJECT_ID && (
          <Script id="ms-clarity" strategy="afterInteractive">
            {`(function(c,l,a,r,i,t,y){
    c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};
    t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;
    y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);
})(window,document,"clarity","script","${process.env.NEXT_PUBLIC_CLARITY_PROJECT_ID}");`}
          </Script>
        )}
      </body>
    </html>
  );
}
