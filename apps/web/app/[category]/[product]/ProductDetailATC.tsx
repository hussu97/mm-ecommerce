'use client';

import { useState } from 'react';
import { useCart } from '@/lib/cart-context';
import { useToast, QuantitySelector } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { analytics } from '@/lib/analytics';
import type { Product } from '@/lib/types';

export function ProductDetailATC({ product }: { product: Product }) {
  const activeVariants = product.variants.filter(v => v.is_active);
  const [selectedVariantId, setSelectedVariantId] = useState(activeVariants[0]?.id ?? '');
  const [qty, setQty] = useState(1);
  const [adding, setAdding] = useState(false);

  const { addItem } = useCart();
  const { addToast } = useToast();

  const selectedVariant = activeVariants.find(v => v.id === selectedVariantId) ?? activeVariants[0];
  const price = selectedVariant?.price ?? product.base_price;
  const outOfStock = selectedVariant ? selectedVariant.stock_quantity === 0 : true;

  const handleAdd = async () => {
    if (!selectedVariant || outOfStock) return;
    setAdding(true);
    try {
      await addItem(selectedVariant.id, qty);
      analytics.addToCart({
        product_name: product.name,
        variant_name: selectedVariant.name,
        price: Number(selectedVariant.price),
        quantity: qty,
      });
      addToast(`${product.name} added to cart`, 'success');
      setQty(1);
    } catch (err) {
      addToast(err instanceof ApiError ? err.message : 'Failed to add to cart', 'error');
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      {/* Price */}
      <span className="font-body text-2xl font-medium text-primary">
        {Number(price).toFixed(2)} AED
      </span>

      {/* Variant selector */}
      {activeVariants.length > 1 && (
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-body uppercase tracking-widest text-gray-500">
            Option
          </label>
          <select
            value={selectedVariantId}
            onChange={e => {
              setSelectedVariantId(e.target.value);
              setQty(1);
            }}
            className="w-full border border-gray-300 px-3 py-2.5 text-sm font-body text-gray-700 bg-white focus:outline-none focus:border-primary transition-colors"
            aria-label="Select variant"
          >
            {activeVariants.map(v => (
              <option key={v.id} value={v.id} disabled={v.stock_quantity === 0}>
                {v.name} — {Number(v.price).toFixed(2)} AED
                {v.stock_quantity === 0 ? ' (Out of Stock)' : ''}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Quantity + ATC */}
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
          className="flex-1 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {adding ? 'Adding...' : outOfStock ? 'Out of Stock' : 'Add to Cart'}
        </button>
      </div>

      {outOfStock && (
        <p className="text-xs font-body text-gray-400 text-center">
          This item is currently out of stock.
        </p>
      )}
    </div>
  );
}
