'use client';

import { useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useCart } from '@/lib/cart-context';
import { useToast, QuantitySelector } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { analytics } from '@/lib/analytics';
import { ModifierModal } from '@/components/product/ModifierModal';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
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

/** Compute "from" price: base_price + cheapest required option per modifier.
 *  If the result is still 0 (e.g. base_price=0 and all modifiers are optional),
 *  fall back to the cheapest option across any modifier so the card never shows "From 0.00 AED". */
function computeFromPrice(product: Product): number {
  let price = Number(product.base_price);
  for (const pm of product.product_modifiers ?? []) {
    if (pm.minimum_options <= 0) continue;
    const activeOptions = pm.modifier.options.filter(o => o.is_active);
    if (activeOptions.length === 0) continue;
    const cheapest = Math.min(...activeOptions.map(o => Number(o.price)));
    price += cheapest * Math.min(pm.minimum_options, 1);
  }
  // If price is still 0 but the product has modifier options, show the cheapest possible price
  if (price === 0 && (product.product_modifiers?.length ?? 0) > 0) {
    for (const pm of product.product_modifiers ?? []) {
      const activeOptions = pm.modifier.options.filter(o => o.is_active);
      if (activeOptions.length === 0) continue;
      const cheapest = Math.min(...activeOptions.map(o => Number(o.price)));
      if (cheapest > 0) { price = cheapest; break; }
    }
  }
  return price;
}

export function ProductCard({ product }: { product: Product }) {
  const { t, locale } = useTranslation();
  const hasModifiers = product.product_modifiers && product.product_modifiers.length > 0;
  const isOutOfStock = product.is_stock_product && product.stock_quantity <= 0;
  const [qty, setQty] = useState(1);
  const [adding, setAdding] = useState(false);
  const [showModal, setShowModal] = useState(false);

  const { addItem } = useCart();
  const { addToast } = useToast();

  const fromPrice = computeFromPrice(product);
  const image = product.image_urls?.[0];
  const categorySlug = product.category?.slug;
  const pdpHref = categorySlug ? `/${locale}/${categorySlug}/${product.slug}` : null;
  const productName = localizedField(product, 'name', product.name, locale);

  // Simple add (no modifiers)
  const handleSimpleAdd = async () => {
    setAdding(true);
    try {
      await addItem(product.id, qty, []);
      analytics.addToCart({
        product_name: product.name,
        variant_name: '',
        price: Number(product.base_price),
        quantity: qty,
      });
      addToast(t('product.added_to_cart', { name: productName }), 'success');
      setQty(1);
    } catch (err) {
      addToast(err instanceof ApiError ? err.message : t('product.failed_to_add'), 'error');
    } finally {
      setAdding(false);
    }
  };

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

          {/* Add to cart controls */}
          {isOutOfStock ? (
            <button
              disabled
              className="w-full py-2.5 bg-gray-100 text-gray-400 text-xs font-body uppercase tracking-widest cursor-not-allowed"
            >
              {t('product.out_of_stock')}
            </button>
          ) : hasModifiers ? (
            <button
              onClick={() => setShowModal(true)}
              className="w-full py-2.5 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
            >
              {t('product.select_options')}
            </button>
          ) : (
            <div className="flex items-center gap-3">
              <QuantitySelector
                value={qty}
                onChange={setQty}
                min={1}
                max={99}
                disabled={adding}
              />
              <button
                onClick={handleSimpleAdd}
                disabled={adding}
                className="flex-1 py-2.5 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {adding ? t('product.adding') : t('product.add_short')}
              </button>
            </div>
          )}
        </div>

      </article>

      {showModal && (
        <ModifierModal product={product} onClose={() => setShowModal(false)} />
      )}
    </>
  );
}
