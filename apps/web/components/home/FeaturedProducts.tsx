'use client';

import { useState, useRef, useEffect } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useCart } from '@/lib/cart-context';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import type { Product } from '@/lib/types';

export interface FeaturedContent {
  title?: string;
  view_all_text?: string;
  view_all_href?: string;
}

function ProductCard({ product }: { product: Product }) {
  const { t, locale } = useTranslation();
  const { addItem } = useCart();
  const [adding, setAdding] = useState(false);

  const hasModifiers = product.product_modifiers && product.product_modifiers.length > 0;
  const price = Number(product.base_price);
  const image = product.image_urls?.[0];
  const categorySlug = product.category?.slug ?? 'products';
  const productName = localizedField(product, 'name', product.name, locale);
  const pdpHref = `/${locale}/${categorySlug}/${product.slug}`;

  const handleAdd = async () => {
    if (hasModifiers) {
      window.location.href = pdpHref;
      return;
    }
    setAdding(true);
    try {
      await addItem(product.id, 1, []);
    } finally {
      setAdding(false);
    }
  };

  return (
    <article className="group flex flex-col flex-shrink-0 w-64 sm:w-auto">
      <Link
        href={pdpHref}
        className="block relative aspect-square overflow-hidden bg-[#f9f5f0]"
      >
        {image ? (
          <Image
            src={image}
            alt={productName}
            fill
            sizes="(max-width: 640px) 256px, (max-width: 1024px) 33vw, 25vw"
            className="object-cover group-hover:scale-105 transition-transform duration-500"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <span className="material-icons text-5xl text-secondary">cake</span>
          </div>
        )}
      </Link>

      <div className="pt-3 flex flex-col gap-2">
        <Link
          href={pdpHref}
          className="font-body text-sm text-gray-800 hover:text-primary transition-colors leading-snug"
        >
          {productName}
        </Link>
        <div className="h-px bg-secondary/40" />
        <div className="flex items-center justify-between gap-2">
          <span className="font-body text-sm font-medium text-primary">
            {hasModifiers ? `${t('product.from')} ` : ''}{price.toFixed(2)} AED
          </span>
          <button
            onClick={handleAdd}
            disabled={adding}
            className="text-[10px] font-body uppercase tracking-widest px-3 py-1.5 border border-primary text-primary hover:bg-primary hover:text-white transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {adding ? '...' : hasModifiers ? t('product.select_short') : t('product.add_short')}
          </button>
        </div>
      </div>
    </article>
  );
}

export function FeaturedProducts({
  products,
  c,
  locale,
}: {
  products: Product[];
  c: FeaturedContent;
  locale: string;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const viewAllHref = `/${locale}${c.view_all_href ?? '/all-products'}`;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 0);
    setCanScrollRight(el.scrollLeft < el.scrollWidth - el.clientWidth - 1);
  }, [products.length]);

  if (products.length === 0) return null;

  const updateArrows = () => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 0);
    setCanScrollRight(el.scrollLeft < el.scrollWidth - el.clientWidth - 1);
  };

  const scrollBy = (dir: 'left' | 'right') => {
    scrollRef.current?.scrollBy({ left: dir === 'left' ? -280 : 280, behavior: 'smooth' });
  };

  return (
    <section aria-label="Featured products" className="py-16 bg-white">
      <div className="max-w-7xl mx-auto px-4">

        <div className="flex items-baseline justify-between mb-8">
          <h2 className="font-display text-2xl sm:text-3xl text-gray-800 uppercase tracking-widest">
            {c.title}
          </h2>
          <Link
            href={viewAllHref}
            className="text-xs font-body text-primary uppercase tracking-widest hover:underline hidden sm:inline"
          >
            {c.view_all_text}
          </Link>
        </div>

        {/* Mobile: horizontal scroll with arrows */}
        <div className="relative sm:hidden">
          {canScrollLeft && (
            <button
              onClick={() => scrollBy('left')}
              className="absolute left-0 top-1/2 -translate-y-1/2 z-10 bg-white/90 rounded-full p-1 shadow-md -ml-2"
              aria-label="Scroll left"
            >
              <span className="material-icons text-primary text-xl">chevron_left</span>
            </button>
          )}
          <div
            ref={scrollRef}
            onScroll={updateArrows}
            className="flex gap-5 overflow-x-auto pb-4 -mx-4 px-4 snap-x snap-mandatory scrollbar-none"
          >
            {products.map((product) => (
              <div key={product.id} className="snap-start">
                <ProductCard product={product} />
              </div>
            ))}
          </div>
          {canScrollRight && (
            <button
              onClick={() => scrollBy('right')}
              className="absolute right-0 top-1/2 -translate-y-1/2 z-10 bg-white/90 rounded-full p-1 shadow-md -mr-2"
              aria-label="Scroll right"
            >
              <span className="material-icons text-primary text-xl">chevron_right</span>
            </button>
          )}
        </div>

        {/* Desktop: grid */}
        <div className="hidden sm:grid sm:grid-cols-2 lg:grid-cols-4 gap-5">
          {products.map((product) => (
            <ProductCard key={product.id} product={product} />
          ))}
        </div>

        <div className="mt-8 text-center sm:hidden">
          <Link
            href={viewAllHref}
            className="text-xs font-body text-primary uppercase tracking-widest hover:underline"
          >
            {c.view_all_text}
          </Link>
        </div>

      </div>
    </section>
  );
}
