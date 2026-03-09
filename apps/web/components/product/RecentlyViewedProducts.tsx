'use client';

import { useEffect, useState } from 'react';
import { useRecentlyViewed } from '@/hooks/useRecentlyViewed';
import { API_BASE } from '@/lib/api';
import { ProductCarousel } from './ProductCarousel';
import type { Product } from '@/lib/types';

export function RecentlyViewedProducts({
  currentSlug,
}: {
  currentSlug: string;
  locale?: string; // kept for backward compat, unused (ProductCard reads locale from context)
}) {
  const slugs = useRecentlyViewed(currentSlug);
  const [products, setProducts] = useState<Product[]>([]);

  useEffect(() => {
    if (!slugs.length) return;
    Promise.all(
      slugs.map(slug =>
        fetch(`${API_BASE}/products/${slug}`)
          .then(r => (r.ok ? r.json() : null))
          .catch(() => null)
      )
    ).then(results => {
      setProducts(results.filter(Boolean) as Product[]);
    });
  }, [slugs]);

  if (!products.length) return null;

  return (
    <div className="max-w-7xl mx-auto px-4 pb-16">
      <ProductCarousel title="Recently Viewed" products={products} />
    </div>
  );
}
