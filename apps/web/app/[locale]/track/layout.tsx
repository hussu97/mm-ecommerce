import type { Metadata } from 'next';

/**
 * Order tracking is a private lookup, not a page for the index.
 *
 * `/track` takes an order number + email and shows one customer their order —
 * there is nothing to crawl and a URL that reads as "someone's order" is one we
 * do not want listed, exactly like the basket and the checkout. Those two answer
 * a crawler with `noindex` from their own layouts (`page.tsx` is a client
 * component and cannot export metadata); `/track` was the one private surface
 * that missed the pair, so a crawler that found the link could list it.
 *
 * `follow: false` with it: what little it links to is reachable from the
 * catalogue, so there is nothing here worth following either.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function TrackLayout({ children }: { children: React.ReactNode }) {
  return children;
}
