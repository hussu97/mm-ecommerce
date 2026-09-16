'use client';

import * as Sentry from '@sentry/nextjs';
import Link from 'next/link';
import { useEffect } from 'react';
import { Icon } from '@/components/ui/Icon';

import { analytics } from '@/lib/analytics';

/**
 * The shared body of every segment `error.tsx`.
 *
 * A single `[locale]/error.tsx` catches everything, but it replaces the whole
 * subtree with one generic message and one "Back to Home" — which on the
 * checkout throws away the cart and tells a shopper mid-purchase to start over
 * at the top of the site. A segment boundary contains the blast radius: the rest
 * of the app's chrome stays, and the recovery link points somewhere useful for
 * *that* segment (F-WEB-11). The reporting is identical to the root boundary —
 * Sentry for the stack, Umami for the journey — so a spike is still one number
 * in Sentry and readable against the funnel it broke here.
 */
export function SegmentError({
  error,
  reset,
  title,
  message,
  backHref,
  backLabel,
}: {
  error: Error & { digest?: string };
  reset: () => void;
  title: string;
  message: string;
  backHref: string;
  backLabel: string;
}) {
  useEffect(() => {
    Sentry.captureException(error);
    analytics.appError({ digest: error.digest, path: window.location.pathname });
    console.error(error);
  }, [error]);

  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center px-4 text-center">
      <Icon name="error_outline" className="text-6xl text-secondary mb-4" />
      <h1 className="font-display text-2xl sm:text-3xl text-primary uppercase tracking-widest mb-4">
        {title}
      </h1>
      <p className="font-body text-sm text-gray-500 max-w-sm mb-8">{message}</p>
      <div className="flex flex-col sm:flex-row gap-3">
        <button
          onClick={reset}
          className="px-6 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
        >
          Try Again
        </button>
        <Link
          href={backHref}
          className="px-6 py-3 border border-gray-300 text-gray-600 text-xs font-body uppercase tracking-widest hover:bg-gray-50 transition-colors"
        >
          {backLabel}
        </Link>
      </div>
      {error.digest && (
        <p className="mt-6 font-body text-[11px] text-gray-300">Error ID: {error.digest}</p>
      )}
    </div>
  );
}
