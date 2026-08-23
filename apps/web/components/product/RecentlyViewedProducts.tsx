'use client';

import { useEffect, useState } from 'react';
import { useRecentlyViewed } from '@/hooks/useRecentlyViewed';
import { productsApi } from '@/lib/api';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { ProductCarousel } from './ProductCarousel';
import type { Product } from '@/lib/types';

export function RecentlyViewedProducts({
  currentSlug,
}: {
  currentSlug: string;
}) {
  const { t } = useTranslation();
  const slugs = useRecentlyViewed(currentSlug);
  const [products, setProducts] = useState<Product[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!slugs.length) return;
    setIsLoading(true);
    Promise.all(
      // Through `productsApi` rather than a bare fetch, per convention 9. The
      // bare one skipped the 401 refresh and — more to the point here — the
      // `api_error` hook, so a failure on this carousel was invisible to
      // Umami while every other endpoint's was reported.
      slugs.map(slug => productsApi.bySlug(slug).catch(() => null))
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
      <ProductCarousel title={t('product.recently_viewed')} products={products} list="recently_viewed" />
    </div>
  );
}
