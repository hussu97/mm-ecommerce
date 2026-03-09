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
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!slugs.length) return;
    setIsLoading(true);
    Promise.all(
      slugs.map(slug =>
        fetch(`${API_BASE}/products/${slug}`)
          .then(r => (r.ok ? r.json() : null))
          .catch(() => null)
      )
    ).then(results => {
      setProducts(results.filter(Boolean) as Product[]);
      setIsLoading(false);
    });
  }, [slugs]);

  if (isLoading) {
    return (
      <div className="max-w-7xl mx-auto px-4 pb-16">
        <div className="flex gap-4 overflow-hidden">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="w-56 shrink-0 rounded-xl bg-gray-100 animate-pulse aspect-square" />
          ))}
        </div>
      </div>
    );
  }

  if (!products.length) return null;

  return (
    <div className="max-w-7xl mx-auto px-4 pb-16">
      <ProductCarousel title="Recently Viewed" products={products} />
    </div>
  );
}
