'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useRecentlyViewed } from '@/hooks/useRecentlyViewed';
import { localizedField } from '@/lib/i18n/entity';
import type { Product } from '@/lib/types';
import { API_BASE } from '@/lib/api';

export function RecentlyViewedProducts({
  currentSlug,
  locale,
}: {
  currentSlug: string;
  locale: string;
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
      <h2 className="font-display text-sm uppercase tracking-widest text-gray-400 mb-6">
        Recently Viewed
      </h2>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
        {products.map(product => {
          const name = localizedField(product, 'name', product.name, locale);
          const categorySlug = product.category?.slug ?? 'products';
          const image = product.image_urls?.[0];
          return (
            <Link
              key={product.id}
              href={`/${locale}/${categorySlug}/${product.slug}`}
              className="group block"
            >
              <div className="relative aspect-square bg-gray-50 overflow-hidden mb-2">
                {image ? (
                  <Image
                    src={image}
                    alt={name}
                    fill
                    className="object-cover group-hover:scale-105 transition-transform duration-300"
                    sizes="(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 20vw"
                  />
                ) : (
                  <div className="absolute inset-0 bg-secondary/20" />
                )}
              </div>
              <p className="text-xs font-body text-gray-700 group-hover:text-primary transition-colors line-clamp-2">
                {name}
              </p>
              <p className="text-xs font-body text-gray-400 mt-0.5">
                {Number(product.base_price).toFixed(2)} AED
              </p>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
