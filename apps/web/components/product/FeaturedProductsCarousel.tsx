'use client';

import { useEffect, useState } from 'react';
import { productsApi } from '@/lib/api';
import { ProductCarousel } from './ProductCarousel';
import type { Product } from '@/lib/types';

export function FeaturedProductsCarousel({
  title = 'You May Also Like',
  limit = 10,
}: {
  title?: string;
  limit?: number;
}) {
  const [products, setProducts] = useState<Product[]>([]);

  useEffect(() => {
    productsApi.featured(limit).then(setProducts).catch(() => undefined);
  }, [limit]);

  if (!products.length) return null;

  return <ProductCarousel title={title} products={products} />;
}
