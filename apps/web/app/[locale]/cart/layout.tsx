import type { Metadata } from 'next';

/**
 * The basket exists so it can be crawled and then refused.
 *
 * `robots.txt` used to `Disallow: /cart`, which stopped the fetch without
 * stopping the listing: the header links to the basket from every page, so the
 * URL was well known, and a URL a crawler may not read is one it can only index
 * as a naked link. Bing's site scan reported it. Allowing the fetch and
 * answering with `noindex` is the pair that works — the crawler reads the page,
 * finds the directive, and drops the URL.
 *
 * `follow: false` with it: everything the basket links to is reachable from the
 * catalogue, and the one link it adds is the checkout, which is the next thing
 * we do not want listed.
 *
 * A layout rather than the page, because `page.tsx` is a client component and
 * cannot export metadata.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function CartLayout({ children }: { children: React.ReactNode }) {
  return children;
}
