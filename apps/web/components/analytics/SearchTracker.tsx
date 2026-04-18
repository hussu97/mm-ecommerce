'use client';

import { useEffect } from 'react';
import { analytics } from '@/lib/analytics';

export function SearchTracker({ query, resultCount }: { query: string; resultCount: number }) {
  useEffect(() => {
    analytics.search({ query, result_count: resultCount });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);
  return null;
}
