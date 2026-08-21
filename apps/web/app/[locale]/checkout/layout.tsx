import type { Metadata } from 'next';

/**
 * Crawlable and unlisted, for the same reason as the basket next door — see
 * `../cart/layout.tsx`. This covers the confirmation page under it too, which
 * carries an order number and belongs in a search index even less than this
 * one does.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function CheckoutLayout({ children }: { children: React.ReactNode }) {
  return children;
}
