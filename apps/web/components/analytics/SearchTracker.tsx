'use client';

import { useEffect } from 'react';
import { analytics } from '@/lib/analytics';

export function SearchTracker({
  query,
  resultCount,
  category,
}: {
  query: string;
  resultCount: number;
  category?: string;
}) {
  useEffect(() => {
    analytics.search({ query, result_count: resultCount });
    /**
     * A search that found nothing, as an event of its own.
     *
     * `search` has always carried `result_count`, so in principle this was
     * already derivable — but Umami matches goals and funnel steps on the event
     * name, so a derivable fact is not something you can watch, alert on, or
     * measure drop-off across. It is also the single most actionable list the
     * shop has: every empty query is either a product worth stocking or a word
     * worth teaching the index.
     */
    if (resultCount === 0) {
      analytics.searchNoResults({ query, category });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);
  return null;
}
