'use client';

import { useEffect, useState } from 'react';
import { productsApi } from '@/lib/api';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { ProductCarousel } from './ProductCarousel';
import type { Product } from '@/lib/types';

export function FeaturedProductsCarousel({
  limit = 10,
}: {
  limit?: number;
}) {
  const { t } = useTranslation();
  const [products, setProducts] = useState<Product[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    productsApi.featured(limit)
      .then(setProducts)
      .catch(() => undefined)
      .finally(() => setIsLoading(false));
  }, [limit]);

  if (isLoading) {
    return (
      <div className="flex gap-4 overflow-hidden px-4">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="w-56 shrink-0 rounded-xl bg-gray-100 animate-pulse aspect-square" />
        ))}
      </div>
    );
  }

  if (!products.length) return null;

  return <ProductCarousel title={t('product.you_may_also_like')} products={products} />;
}
