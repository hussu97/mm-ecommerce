'use client';

import { useState } from 'react';
import Image from 'next/image';
import { useCart } from '@/lib/cart-context';
import { useToast, QuantitySelector } from '@/components/ui';
import { ApiError } from '@/lib/api';
import type { Product } from '@/lib/types';

export function ProductCard({ product }: { product: Product }) {
  const activeVariants = product.variants.filter(v => v.is_active);
  const [selectedVariantId, setSelectedVariantId] = useState(activeVariants[0]?.id ?? '');
  const [qty, setQty] = useState(1);
  const [adding, setAdding] = useState(false);

  const { addItem } = useCart();
  const { addToast } = useToast();

  const selectedVariant = activeVariants.find(v => v.id === selectedVariantId) ?? activeVariants[0];
  const price = selectedVariant?.price ?? product.base_price;
  const outOfStock = selectedVariant ? selectedVariant.stock_quantity === 0 : true;
  const image = product.image_urls?.[0];

  const handleAdd = async () => {
    if (!selectedVariant || outOfStock) return;
    setAdding(true);
    try {
      await addItem(selectedVariant.id, qty);
      addToast(`${product.name} added to cart`, 'success');
      setQty(1);
    } catch (err) {
      addToast(err instanceof ApiError ? err.message : 'Failed to add to cart', 'error');
    } finally {
      setAdding(false);
    }
  };

  return (
    <article className="flex flex-col group">

      {/* Image */}
      <div className="relative aspect-square overflow-hidden bg-[#f9f5f0]">
        {image ? (
          <Image
            src={image}
            alt={product.name}
            fill
            sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
            className="object-cover group-hover:scale-105 transition-transform duration-500"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <span className="material-icons text-6xl text-secondary">cake</span>
          </div>
        )}
        {outOfStock && (
          <div className="absolute inset-0 bg-white/60 flex items-center justify-center">
            <span className="text-xs font-body uppercase tracking-widest text-gray-500 bg-white px-3 py-1.5 border border-gray-300">
              Out of Stock
            </span>
          </div>
        )}
      </div>

      {/* Details */}
      <div className="pt-4 flex flex-col gap-3">
        <h3 className="font-display text-base text-gray-800 leading-snug">
          {product.name}
        </h3>
        <div className="h-px bg-secondary/40" />

        {/* Price */}
        <span className="font-body text-base font-medium text-primary">
          {Number(price).toFixed(2)} AED
        </span>

        {/* Variant selector — only when > 1 active variant */}
        {activeVariants.length > 1 && (
          <select
            value={selectedVariantId}
            onChange={e => {
              setSelectedVariantId(e.target.value);
              setQty(1);
            }}
            className="w-full border border-gray-300 px-3 py-2 text-sm font-body text-gray-700 bg-white focus:outline-none focus:border-primary transition-colors"
            aria-label="Select variant"
          >
            {activeVariants.map(v => (
              <option key={v.id} value={v.id} disabled={v.stock_quantity === 0}>
                {v.name} — {Number(v.price).toFixed(2)} AED
                {v.stock_quantity === 0 ? ' (Out of Stock)' : ''}
              </option>
            ))}
          </select>
        )}

        {/* Qty + Add to cart */}
        <div className="flex items-center gap-3">
          <QuantitySelector
            value={qty}
            onChange={setQty}
            min={1}
            max={selectedVariant?.stock_quantity ?? 99}
            disabled={adding || outOfStock}
          />
          <button
            onClick={handleAdd}
            disabled={adding || outOfStock}
            className="flex-1 py-2.5 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {adding ? 'Adding...' : outOfStock ? 'Out of Stock' : 'Add to Cart'}
          </button>
        </div>
      </div>

    </article>
  );
}
