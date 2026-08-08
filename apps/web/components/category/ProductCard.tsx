'use client';

import Image from 'next/image';
import Link from 'next/link';
import { DeliveryEstimate } from '@/components/product/DeliveryEstimate';
import { AddToCartControl } from '@/components/product/AddToCartControl';
import { ProductBadge } from '@/components/product/ProductBadge';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { withFallback } from '@/lib/i18n/fallback';
import { computeFromPrice } from '@/lib/pricing';
import type { Product } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

function ConditionalLink({
  href,
  children,
  className,
}: {
  href: string | null;
  children: React.ReactNode;
  className?: string;
}) {
  if (!href) return <div className={className}>{children}</div>;
  return <Link href={href} className={className}>{children}</Link>;
}

export function ProductCard({
  product,
  badge,
  priority = false,
}: {
  product: Product;
  badge?: string;
  /**
   * Set on the handful of cards that are above the fold.
   *
   * `next/image` is lazy by default, which on a listing page means the LCP
   * element is an image the browser is told not to hurry — it is not even
   * discovered until layout has run. `ProductGrid` sets this on the first row
   * or two; everything below stays lazy, which is what lazy is for.
   */
  priority?: boolean;
}) {
  const { t, locale } = useTranslation();
  const hasModifiers = product.product_modifiers && product.product_modifiers.length > 0;

  const fromPrice = computeFromPrice(product);
  const image = product.image_urls?.[0];
  const categorySlug = product.category?.slug;
  const pdpHref = categorySlug ? `/${locale}/${categorySlug}/${product.slug}` : null;
  const productName = localizedField(product, 'name', product.name, locale);
  // `is_featured` is the flag the admin already sets, and the homepage rail
  // already flies its "Bestseller" corner on those same products — so the
  // listing reads it rather than inventing a second notion of a bestseller.
  const badgeText = product.is_featured
    ? badge ?? withFallback(t, 'plp.bestseller', 'Bestseller')
    : undefined;

  return (
    <>
      <article className="flex flex-col group">

        {/* Image */}
        <ConditionalLink href={pdpHref} className="relative aspect-square overflow-hidden bg-[#f9f5f0] block">
          {image ? (
            <Image
              src={image}
              alt={productName}
              fill
              sizes="(max-width: 640px) 50vw, (max-width: 1024px) 50vw, 33vw"
              priority={priority}
              className="object-cover group-hover:scale-105 transition-transform duration-500"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <Icon name="cake" className="text-5xl sm:text-6xl text-secondary" />
            </div>
          )}
          {badgeText && <ProductBadge>{badgeText}</ProductBadge>}
        </ConditionalLink>

        {/* Details */}
        <div className="pt-3 sm:pt-4 flex flex-col flex-1 gap-2 sm:gap-3">
          <ConditionalLink href={pdpHref}>
            <h3 className="font-display text-sm sm:text-base text-gray-800 leading-snug hover:text-primary transition-colors line-clamp-2 min-h-[2.5rem] sm:min-h-[2.75rem]">
              {productName}
            </h3>
          </ConditionalLink>
          <div className="h-px bg-secondary/40" />

          {/* Price */}
          <span className="font-body text-sm sm:text-base font-medium text-primary">
            {hasModifiers ? `${t('product.from')} ` : ''}{fromPrice.toFixed(2)} AED
          </span>

          {/* Where it lands and how fast, read from the shared location
              context so the card, the product page and the cart cannot show
              three different promises on one journey. */}
          <DeliveryEstimate />

          <AddToCartControl product={product} />
        </div>

      </article>
    </>
  );
}
