'use client';

import Image from 'next/image';
import Link from 'next/link';
import { AddToCartControl } from '@/components/product/AddToCartControl';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { computeFromPrice } from '@/lib/pricing';
import type { Product } from '@/lib/types';

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

export function ProductCard({ product }: { product: Product }) {
  const { t, locale } = useTranslation();
  const hasModifiers = product.product_modifiers && product.product_modifiers.length > 0;

  const fromPrice = computeFromPrice(product);
  const image = product.image_urls?.[0];
  const categorySlug = product.category?.slug;
  const pdpHref = categorySlug ? `/${locale}/${categorySlug}/${product.slug}` : null;
  const productName = localizedField(product, 'name', product.name, locale);

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
              sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
              className="object-cover group-hover:scale-105 transition-transform duration-500"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <span className="material-icons text-6xl text-secondary">cake</span>
            </div>
          )}
        </ConditionalLink>

        {/* Details */}
        <div className="pt-4 flex flex-col flex-1 gap-3">
          <ConditionalLink href={pdpHref}>
            <h3 className="font-display text-base text-gray-800 leading-snug hover:text-primary transition-colors line-clamp-2 min-h-[2.75rem]">
              {productName}
            </h3>
          </ConditionalLink>
          <div className="h-px bg-secondary/40" />

          {/* Price */}
          <span className="font-body text-base font-medium text-primary">
            {hasModifiers ? `${t('product.from')} ` : ''}{fromPrice.toFixed(2)} AED
          </span>

          <AddToCartControl product={product} />
        </div>

      </article>
    </>
  );
}
