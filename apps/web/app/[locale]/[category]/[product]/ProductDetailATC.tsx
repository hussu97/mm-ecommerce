'use client';

import { useState } from 'react';
import { useCart } from '@/lib/cart-context';
import { useToast, QuantitySelector } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { analytics } from '@/lib/analytics';
import { ModifierSelector, SelectedOption } from '@/components/product/ModifierSelector';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import type { Product } from '@/lib/types';

/** Minimum displayable price: base_price + cheapest required option, or cheapest any option if still 0 */
function computeMinPrice(product: Product): number {
  let price = Number(product.base_price);
  for (const pm of product.product_modifiers ?? []) {
    if (pm.minimum_options <= 0) continue;
    const active = pm.modifier.options.filter(o => o.is_active);
    if (active.length === 0) continue;
    price += Math.min(...active.map(o => Number(o.price))) * Math.min(pm.minimum_options, 1);
  }
  if (price === 0 && (product.product_modifiers?.length ?? 0) > 0) {
    for (const pm of product.product_modifiers ?? []) {
      const active = pm.modifier.options.filter(o => o.is_active);
      if (active.length === 0) continue;
      const cheapest = Math.min(...active.map(o => Number(o.price)));
      if (cheapest > 0) { price = cheapest; break; }
    }
  }
  return price;
}

export function ProductDetailATC({ product }: { product: Product }) {
  const { t, locale } = useTranslation();
  const hasModifiers = product.product_modifiers && product.product_modifiers.length > 0;
  const minPrice = hasModifiers ? computeMinPrice(product) : Number(product.base_price);
  const [qty, setQty] = useState(1);
  const [adding, setAdding] = useState(false);
  const [selectedOptions, setSelectedOptions] = useState<SelectedOption[]>([]);
  const [totalPrice, setTotalPrice] = useState(Number(product.base_price));
  const [isValid, setIsValid] = useState(!hasModifiers);

  const { addItem } = useCart();
  const { addToast } = useToast();

  const productName = localizedField(product, 'name', product.name, locale);

  const handleOptionsChange = (opts: SelectedOption[], price: number, valid: boolean) => {
    setSelectedOptions(opts);
    setTotalPrice(price);
    setIsValid(valid);
  };

  const handleAdd = async () => {
    if (!isValid) return;
    setAdding(true);
    try {
      await addItem(product.id, qty, selectedOptions);
      analytics.addToCart({
        product_name: product.name,
        variant_name: selectedOptions.map(o => o.option_id).join('+'),
        price: totalPrice,
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
    <div className="flex flex-col gap-5">
      {/* Price */}
      <span className="font-body text-2xl font-medium text-primary">
        {hasModifiers && !isValid && (
          <span className="text-base font-normal text-gray-400 mr-1">{t('product.from')}</span>
        )}
        {(hasModifiers && !isValid ? minPrice : Number(totalPrice)).toFixed(2)} AED
      </span>

      {/* Modifier selectors */}
      {hasModifiers && (
        <ModifierSelector product={product} onChange={handleOptionsChange} />
      )}

      {/* Quantity + ATC */}
      <div className="flex items-center gap-3">
        <QuantitySelector
          value={qty}
          onChange={setQty}
          min={1}
          max={99}
          disabled={adding || !isValid}
        />
        <button
          onClick={handleAdd}
          disabled={adding || !isValid}
          className="flex-1 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {adding ? t('product.adding') : !isValid ? t('product.select_required_options') : t('product.add_to_cart')}
        </button>
      </div>
    </div>
  );
}
