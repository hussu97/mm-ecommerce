import type { Metadata } from 'next';

import { AccountShell } from './AccountShell';

/**
 * Crawlable and unlisted, for the same reason as the basket — see
 * `../cart/layout.tsx`. `robots.txt` blocking the fetch is what let these URLs
 * be indexed as bare links; `noindex` is the directive that removes them, and
 * it only works if the crawler is allowed in to read it.
 *
 * The signed-in shell moved to `AccountShell` so this file can be a server
 * component: `metadata` cannot be exported from a `'use client'` module, and a
 * route may only have one layout.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function AccountLayout({ children }: { children: React.ReactNode }) {
  return <AccountShell>{children}</AccountShell>;
}
