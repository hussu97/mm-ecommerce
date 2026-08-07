'use client';

import { useEffect } from 'react';
import { analytics } from '@/lib/analytics';

/**
 * Record a 404, and where it was reached from.
 *
 * Umami's page-view list already shows the address, but it cannot tell a page
 * that exists from one that does not — a 404 looks exactly like a successful
 * visit. The referrer is the half that makes it actionable: a dead link inside
 * our own site is ours to fix, one from a search engine is a URL worth
 * redirecting, and the two are indistinguishable without it.
 *
 * A client component because `not-found.tsx` is rendered on the server.
 */
export function NotFoundTracker() {
  useEffect(() => {
    analytics.pageNotFound({
      path: window.location.pathname,
      referrer: document.referrer || undefined,
    });
  }, []);
  return null;
}
