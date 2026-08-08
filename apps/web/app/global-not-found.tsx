import type { Metadata } from 'next';
import Link from 'next/link';
import { Raleway, Jost } from 'next/font/google';

import './globals.css';

/**
 * The 404 for URLs that never reach a language.
 *
 * Almost nothing lands here. `proxy.ts` sends any unprefixed path to a locale
 * before the router sees it, so a missing page is normally answered by
 * `app/[locale]/not-found.tsx`, inside the shell, in the right language. What
 * escapes is the set the proxy's matcher deliberately skips — anything with a
 * dot in it, like `/wp-login.php` or a stale `/sitemap.xml.gz` — which matches
 * no route and has no locale to be rendered in.
 *
 * It exists because there is no longer an `app/layout.tsx` to fall back on: the
 * root layout moved under `[locale]` so the shell could stop reading `cookies()`
 * and start being cacheable. Without this file those URLs got Next's bare
 * `__next_error__` document, which is unstyled and unbranded.
 *
 * Deliberately self-contained — its own `<html>`, its own fonts, no context, no
 * API call. It has to work when the thing that failed is everything else.
 */

// `preload: false` for the same reason as the locale layout, and for one more:
// Next hoists font preloads out of whichever module declares them, so leaving
// it on here put a preload for these two Latin faces in the <head> of every
// page on the site — including the Arabic ones, which render in neither.
const raleway = Raleway({
  subsets: ['latin'],
  weight: ['400', '600'],
  variable: '--font-raleway',
  display: 'swap',
  preload: false,
});

const jost = Jost({
  subsets: ['latin'],
  weight: ['400'],
  variable: '--font-jost',
  display: 'swap',
  preload: false,
});

export const metadata: Metadata = {
  title: '404 — Page Not Found | Melting Moments Cakes',
  description: 'The page you were looking for is not here.',
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com'),
  robots: { index: false, follow: false },
};

export default function GlobalNotFound() {
  return (
    <html lang="en" dir="ltr" className={`${raleway.variable} ${jost.variable}`}>
      <body className="min-h-screen flex flex-col antialiased">
        <div className="min-h-screen flex flex-col items-center justify-center px-4 text-center">
          <p className="font-display text-8xl sm:text-[10rem] text-secondary/60 leading-none select-none">
            404
          </p>
          <h1 className="font-display text-2xl sm:text-3xl text-primary uppercase tracking-widest mt-2 mb-4">
            Page Not Found
          </h1>
          <p className="font-body text-sm text-gray-500 max-w-sm mb-8">
            That address does not lead anywhere. The brownies, however, are exactly where
            you left them.
          </p>
          <Link
            href="/en"
            className="px-6 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
          >
            Back to Home
          </Link>
          <div className="mt-12 w-16 h-0.5 bg-secondary/40" />
          <p className="font-body text-xs text-gray-400 italic mt-3">
            Melting Moments Cakes
          </p>
        </div>
      </body>
    </html>
  );
}
