'use client';

import { useRef, useState, useEffect } from 'react';
import { ProductCard } from '@/components/category/ProductCard';
import type { ProductList } from '@/lib/analytics';
import type { Product } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

export function ProductCarousel({
  title,
  products,
  list = 'carousel',
}: {
  title: string;
  products: Product[];
  /** Which rail this is, so `select_item` can tell them apart. */
  list?: ProductList;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const check = () => {
      setCanScrollLeft(el.scrollLeft > 4);
      setCanScrollRight(el.scrollLeft < el.scrollWidth - el.clientWidth - 4);
    };
    check();
    el.addEventListener('scroll', check, { passive: true });
    window.addEventListener('resize', check);
    return () => {
      el.removeEventListener('scroll', check);
      window.removeEventListener('resize', check);
    };
  }, [products]);

  const scrollBy = (dir: 'left' | 'right') => {
    scrollRef.current?.scrollBy({ left: dir === 'left' ? -280 : 280, behavior: 'smooth' });
  };

  if (!products.length) return null;

  return (
    <div>
      <h2 className="font-display text-sm uppercase tracking-widest text-gray-400 mb-6">
        {title}
      </h2>
      <div className="relative">
        {canScrollLeft && (
          <button
            onClick={() => scrollBy('left')}
            className="absolute left-0 top-1/3 -translate-y-1/2 z-10 -translate-x-4 w-8 h-8 bg-white border border-gray-200 shadow-sm hidden sm:flex items-center justify-center hover:bg-gray-50 transition-colors"
            aria-label="Scroll left"
          >
            <Icon name="chevron_left" className="text-base text-gray-600" />
          </button>
        )}

        <div
          ref={scrollRef}
          className="flex gap-5 overflow-x-auto scrollbar-none scroll-smooth snap-x snap-mandatory pb-2"
        >
          {products.map((product, i) => (
            <div key={product.id} className="w-56 shrink-0 snap-start">
              <ProductCard product={product} list={list} position={i} />
            </div>
          ))}
        </div>

        {canScrollRight && (
          <button
            onClick={() => scrollBy('right')}
            className="absolute right-0 top-1/3 -translate-y-1/2 z-10 translate-x-4 w-8 h-8 bg-white border border-gray-200 shadow-sm hidden sm:flex items-center justify-center hover:bg-gray-50 transition-colors"
            aria-label="Scroll right"
          >
            <Icon name="chevron_right" className="text-base text-gray-600" />
          </button>
        )}
      </div>
    </div>
  );
}
