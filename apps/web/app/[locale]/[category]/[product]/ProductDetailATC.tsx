'use client';

import { useState, useEffect } from 'react';
import { useCart } from '@/lib/cart-context';
import { useToast, QuantitySelector } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { analytics } from '@/lib/analytics';
import { ModifierSelector, SelectedOption } from '@/components/product/ModifierSelector';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { computeFromPrice, isModifierPriced } from '@/lib/pricing';
import type { Product } from '@/lib/types';

export function ProductDetailATC({ product }: { product: Product }) {
  const { t, locale } = useTranslation();
  const hasModifiers = product.product_modifiers && product.product_modifiers.length > 0;
  const isOutOfStock = product.is_stock_product && product.stock_quantity <= 0;
  const minPrice = hasModifiers ? computeFromPrice(product) : Number(product.base_price);
  const [qty, setQty] = useState(1);
  const [adding, setAdding] = useState(false);
  const [selectedOptions, setSelectedOptions] = useState<SelectedOption[]>([]);

  useEffect(() => {
    analytics.viewProduct({
      product_name: localizedField(product, 'name', product.name, locale),
      category: (product.category as { name?: string } | undefined)?.name ?? '',
      price: minPrice,
      has_modifiers: !!hasModifiers,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
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
      {isOutOfStock ? (
        <button
          disabled
          className="w-full py-3 bg-gray-100 text-gray-400 text-xs font-body uppercase tracking-widest cursor-not-allowed"
        >
          {t('product.out_of_stock')}
        </button>
      ) : (
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
      )}
    </div>
  );
}
