'use client';

import { useState, useRef, useEffect } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { computeFromPrice } from '@/lib/pricing';
import { AddToCartControl } from '@/components/product/AddToCartControl';
import { DeliveryEstimate } from '@/components/product/DeliveryEstimate';
import { ProductBadge } from '@/components/product/ProductBadge';
import { Reveal } from './Reveal';
import type { Product } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

export interface FeaturedContent {
  title?: string;
  subtitle?: string;
  view_all_text?: string;
  view_all_href?: string;
  /** Corner flag on every card in this rail, e.g. "Bestseller". Blank hides it. */
  badge_text?: string;
}

function ProductCard({ product, badge }: { product: Product; badge?: string }) {
  const { t, locale } = useTranslation();

  const hasModifiers = product.product_modifiers && product.product_modifiers.length > 0;
  const price = computeFromPrice(product);
  const image = product.image_urls?.[0];
  const categorySlug = product.category?.slug ?? 'products';
  const productName = localizedField(product, 'name', product.name, locale);
  const pdpHref = `/${locale}/${categorySlug}/${product.slug}`;

  return (
    <article className="group flex flex-col flex-shrink-0 w-56 sm:w-auto">
      <Link
        href={pdpHref}
        className="block relative aspect-square overflow-hidden bg-[#f4ece4]"
      >
        {image ? (
          <Image
            src={image}
            alt={productName}
            fill
            sizes="(max-width: 640px) 224px, (max-width: 1024px) 33vw, 25vw"
            className="object-cover transition-transform duration-[900ms] ease-out group-hover:scale-[1.08]"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Icon name="cake" className="text-5xl text-secondary" />
          </div>
        )}

        {badge && <ProductBadge>{badge}</ProductBadge>}

        {/* A wash that lifts on hover — the card feels live without moving the
            layout a single pixel. */}
        <span className="absolute inset-0 bg-primary/0 group-hover:bg-primary/10 transition-colors duration-500 pointer-events-none" />
      </Link>

      <div className="pt-3 flex flex-col gap-2">
        <Link
          href={pdpHref}
          className="font-body text-sm text-gray-800 hover:text-primary transition-colors leading-snug line-clamp-2"
        >
          {productName}
        </Link>
        <div className="h-px bg-secondary/40" />
        <span className="font-body text-sm font-medium text-primary">
          {hasModifiers ? `${t('product.from')} ` : ''}{price.toFixed(2)} AED
        </span>
        {/* This rail has its own card rather than reusing `ProductCard`, which
            is why it was the one product surface with no delivery promise on
            it. Same component, same badge, same position under the price. */}
        <DeliveryEstimate />
        <AddToCartControl product={product} size="compact" />
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
    scrollRef.current?.scrollBy({ left: dir === 'left' ? -240 : 240, behavior: 'smooth' });
  };

  return (
    <section aria-label="Featured products" className="py-14 sm:py-20 bg-white">
      <div className="max-w-7xl mx-auto px-4 sm:px-6">

        <Reveal className="flex items-end justify-between gap-4 mb-7 sm:mb-9">
          <div>
            <h2 className="font-display text-2xl sm:text-3xl text-gray-800 uppercase tracking-[0.18em]">
              {c.title}
            </h2>
            {c.subtitle && (
              <p className="font-body text-sm text-gray-500 mt-2">{c.subtitle}</p>
            )}
          </div>
          {c.view_all_text && (
            <Link
              href={viewAllHref}
              className="hidden sm:inline-flex items-center gap-1.5 shrink-0 border border-primary/60 text-primary text-[11px] font-body uppercase tracking-widest px-4 py-2.5 hover:bg-primary hover:text-white transition-colors duration-200"
            >
              {c.view_all_text}
              <Icon name="arrow_forward" className="text-[14px] rtl:rotate-180" />
            </Link>
          )}
        </Reveal>

        {/* Mobile: a swipeable rail — four cards deep without four screens of scroll. */}
        <div className="relative sm:hidden">
          {canScrollLeft && (
            <button
              onClick={() => scrollBy('left')}
              className="absolute start-0 top-1/3 -translate-y-1/2 z-10 bg-white/90 rounded-full p-1 shadow-md -ms-2"
              aria-label="Scroll left"
            >
              <Icon name="chevron_left" className="text-primary text-xl rtl:rotate-180" />
            </button>
          )}
          <div
            ref={scrollRef}
            onScroll={updateArrows}
            className="mm-rail flex gap-4 overflow-x-auto pb-4 -mx-4 px-4 snap-x snap-mandatory"
          >
            {products.map((product) => (
              <div key={product.id} className="snap-start">
                <ProductCard product={product} badge={c.badge_text} />
              </div>
            ))}
          </div>
          {canScrollRight && (
            <button
              onClick={() => scrollBy('right')}
              className="absolute end-0 top-1/3 -translate-y-1/2 z-10 bg-white/90 rounded-full p-1 shadow-md -me-2"
              aria-label="Scroll right"
            >
              <Icon name="chevron_right" className="text-primary text-xl rtl:rotate-180" />
            </button>
          )}
        </div>

        {/* Desktop: the grid, cascading in as it enters the viewport. */}
        <div className="hidden sm:grid sm:grid-cols-2 lg:grid-cols-4 gap-5">
          {products.map((product, i) => (
            <Reveal key={product.id} delay={Math.min(i, 7) * 70}>
              <ProductCard product={product} badge={c.badge_text} />
            </Reveal>
          ))}
        </div>

        {c.view_all_text && (
          <div className="mt-7 sm:hidden">
            <Link
              href={viewAllHref}
              className="flex items-center justify-center gap-1.5 border border-primary/60 text-primary text-[11px] font-body uppercase tracking-widest px-4 py-3 hover:bg-primary hover:text-white transition-colors duration-200"
            >
              {c.view_all_text}
              <Icon name="arrow_forward" className="text-[14px] rtl:rotate-180" />
            </Link>
          </div>
        )}

      </div>
    </section>
  );
}
