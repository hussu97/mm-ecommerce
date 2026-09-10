import type { Metadata } from 'next';

import { API_BASE } from '@/lib/api-base';

/**
 * Crawlable and unlisted, for the same reason as the basket next door — see
 * `../cart/layout.tsx`. This covers the confirmation page under it too, which
 * carries an order number and belongs in a search index even less than this
 * one does.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

/**
 * Origins the checkout opens a socket to before it has rendered anything the
 * customer waits on — the delivery-preview call the moment the basket loads, and
 * the Stripe/Apple Pay probe beside it. Warmed with `preconnect` so that first
 * call does not each pay a cold DNS+TCP+TLS handshake on the one screen where a
 * slow first byte is felt.
 *
 * A hint and nothing else: it issues no request, sends no data, and changes no
 * behaviour. A browser that ignores it lands exactly where it does today, and a
 * socket the page never reuses is thrown away by the browser on its own. So this
 * cannot touch the user journey — only shorten the wait in front of it.
 */
interface Preconnect {
  href: string;
  /** How the real request that reuses this socket is made, so the hint matches it. */
  crossOrigin?: 'use-credentials' | 'anonymous';
}

function preconnectOrigins(): Preconnect[] {
  const out: Preconnect[] = [];

  // The API is a different origin in production (`api.<host>`) and relative in
  // dev (same-origin through the Next rewrite, where a preconnect is a no-op).
  // Credentialed, because every `api-client` fetch sends the session cookie and
  // the browser only reuses a pre-warmed socket when its crossorigin mode is the
  // one the real request uses.
  try {
    if (/^https?:\/\//i.test(API_BASE)) {
      out.push({ href: new URL(API_BASE).origin, crossOrigin: 'use-credentials' });
    }
  } catch {
    /* a malformed base is not worth failing a render over */
  }

  // Stripe, only where a key is configured — otherwise Apple Pay never loads
  // js.stripe.com and the hint would warm a socket nothing uses. The SDK script
  // is a plain (no-cors) GET, so it is preconnected without a crossorigin mode;
  // the SDK's own calls to api.stripe.com are anonymous CORS.
  if (process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY) {
    out.push({ href: 'https://js.stripe.com' });
    out.push({ href: 'https://api.stripe.com', crossOrigin: 'anonymous' });
  }

  return out;
}

export default function CheckoutLayout({ children }: { children: React.ReactNode }) {
  const origins = preconnectOrigins();
  return (
    <>
      {origins.map(({ href, crossOrigin }) => (
        <link key={href} rel="preconnect" href={href} crossOrigin={crossOrigin} />
      ))}
      {children}
    </>
  );
}
