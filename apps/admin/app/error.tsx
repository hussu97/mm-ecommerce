"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-neutral-50 px-6">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-semibold text-neutral-900">Something went wrong</h1>
        <p className="mt-3 text-sm text-neutral-600">
          The admin dashboard hit an unexpected error.
        </p>
        <button
          type="button"
          onClick={reset}
          className="mt-6 rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white"
        >
          Try again
        </button>
        {error.digest && <p className="mt-4 text-xs text-neutral-400">Error ID: {error.digest}</p>}
      </div>
    </main>
  );
}
