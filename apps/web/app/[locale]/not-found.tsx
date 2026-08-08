import type { Metadata } from 'next';

import { NotFoundView } from './NotFoundView';

export const metadata: Metadata = { title: '404 — Page Not Found' };

/**
 * The 404 for anything under a locale — which, thanks to the proxy, is very
 * nearly everything. `global-not-found.tsx` covers the handful of URLs that
 * never reach a language at all.
 *
 * A server component only so it can carry `metadata`; the visible half is in
 * `NotFoundView`, which needs the translation context. See the note there.
 */
export default function NotFound() {
  return <NotFoundView />;
}
