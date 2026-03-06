'use client';

import { useEffect, useState } from 'react';

const KEY = 'mm_recently_viewed';
const MAX_SLUGS = 10;
const MAX_DISPLAY = 5;

export function useRecentlyViewed(currentSlug: string): string[] {
  const [slugs, setSlugs] = useState<string[]>([]);

  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(KEY) ?? '[]') as string[];
      // Prepend current slug, dedup, cap at MAX_SLUGS
      const updated = [currentSlug, ...stored.filter(s => s !== currentSlug)].slice(0, MAX_SLUGS);
      localStorage.setItem(KEY, JSON.stringify(updated));
      // Return all except the current one, up to MAX_DISPLAY
      setSlugs(updated.filter(s => s !== currentSlug).slice(0, MAX_DISPLAY));
    } catch {
      // localStorage unavailable
    }
  }, [currentSlug]);

  return slugs;
}
