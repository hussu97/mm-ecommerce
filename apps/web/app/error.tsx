'use client';

import Link from 'next/link';
import { useEffect } from 'react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_SENTRY_DSN) {
      import('@sentry/nextjs')
        .then((Sentry) => Sentry.captureException(error))
        .catch(() => console.error(error));
    } else {
      console.error(error);
    }
  }, [error]);

  return (
    <div className="min-h-[70vh] flex flex-col items-center justify-center px-4 text-center">
      <span className="material-icons text-6xl text-secondary mb-4">error_outline</span>
      <h1 className="font-display text-2xl sm:text-3xl text-primary uppercase tracking-widest mb-4">
        Something Went Wrong
      </h1>
      <p className="font-body text-sm text-gray-500 max-w-sm mb-8">
        We ran into an unexpected error. Please try again — if the problem persists, contact us via WhatsApp.
      </p>
      <div className="flex flex-col sm:flex-row gap-3">
        <button
          onClick={reset}
          className="px-6 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
        >
          Try Again
        </button>
        <Link
          href="/"
          className="px-6 py-3 border border-gray-300 text-gray-600 text-xs font-body uppercase tracking-widest hover:bg-gray-50 transition-colors"
        >
          Back to Home
        </Link>
      </div>
      {error.digest && (
        <p className="mt-6 font-body text-[11px] text-gray-300">Error ID: {error.digest}</p>
      )}
    </div>
  );
}
