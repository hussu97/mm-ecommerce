'use client';

import { useEffect } from 'react';
import { analytics } from '@/lib/analytics';

export function CategoryTracker({ categoryName, productCount }: { categoryName: string; productCount: number }) {
  useEffect(() => {
    analytics.viewCategory({ category_name: categoryName, product_count: productCount });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryName]);
  return null;
}
