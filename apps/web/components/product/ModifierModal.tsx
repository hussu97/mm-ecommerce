'use client';

import { useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { useCart } from '@/lib/cart-context';
import { useToast } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { analytics } from '@/lib/analytics';
import { ModifierSelector, SelectedOption } from './ModifierSelector';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import type { Product } from '@/lib/types';

interface Props {
  product: Product;
  onClose: () => void;
}

export function ModifierModal({ product, onClose }: Props) {
  const { t, locale } = useTranslation();
  const { addItem } = useCart();
  const { addToast } = useToast();
  const overlayRef = useRef<HTMLDivElement>(null);

  const [selectedOptions, setSelectedOptions] = useState<SelectedOption[]>([]);
  const [totalPrice, setTotalPrice] = useState(Number(product.base_price));
  const [isValid, setIsValid] = useState(product.product_modifiers.length === 0);
  const [qty, setQty] = useState(1);
  const [adding, setAdding] = useState(false);

  const productName = localizedField(product, 'name', product.name, locale);
  const productDescription = localizedField(product, 'description', product.description ?? '', locale);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // Lock body scroll
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

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
      onClose();
    } catch (err) {
      addToast(err instanceof ApiError ? err.message : t('product.failed_to_add'), 'error');
    } finally {
      setAdding(false);
    }
  };

  const image = product.image_urls?.[0];

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
      onClick={e => { if (e.target === overlayRef.current) onClose(); }}
    >
      <div className="bg-white w-full max-w-md max-h-[90vh] flex flex-col shadow-xl">
        {/* Header */}
        <div className="flex items-start gap-3 p-5 border-b border-gray-100">
          {image && (
            <div className="relative w-16 h-16 shrink-0">
              <Image src={image} alt={productName} fill sizes="64px" className="object-cover" />
            </div>
          )}
          <div className="flex-1 min-w-0">
            <h2 className="font-display text-base text-gray-800 leading-snug">{productName}</h2>
            {productDescription && (
              <p className="text-xs text-gray-400 font-body mt-0.5 line-clamp-2">{productDescription}</p>
            )}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 shrink-0">
            <span className="material-icons text-[20px]">close</span>
          </button>
        </div>

        {/* Scrollable options */}
        <div className="flex-1 overflow-y-auto p-5">
          <ModifierSelector product={product} onChange={handleOptionsChange} />
        </div>

        {/* Footer */}
        <div className="border-t border-gray-100 p-5 space-y-3">
          {/* Qty selector */}
          <div className="flex items-center gap-3">
            <span className="text-xs font-body uppercase tracking-widest text-gray-500">{t('common.qty')}</span>
            <div className="flex items-center border border-gray-300">
              <button
                type="button"
                onClick={() => setQty(q => Math.max(1, q - 1))}
                disabled={qty <= 1}
                className="w-8 h-8 flex items-center justify-center text-gray-500 hover:text-primary disabled:opacity-40"
              >
                <span className="material-icons text-[16px]">remove</span>
              </button>
              <span className="w-8 text-center text-sm font-body">{qty}</span>
              <button
                type="button"
                onClick={() => setQty(q => q + 1)}
                className="w-8 h-8 flex items-center justify-center text-gray-500 hover:text-primary"
              >
                <span className="material-icons text-[16px]">add</span>
              </button>
            </div>
            <span className="ml-auto font-body font-medium text-primary text-base">
              {(totalPrice * qty).toFixed(2)} AED
            </span>
          </div>

          <button
            onClick={handleAdd}
            disabled={adding || !isValid}
            className="w-full py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {adding ? t('product.adding') : !isValid ? t('product.select_required_options') : t('product.add_to_cart')}
          </button>
        </div>
      </div>
    </div>
  );
}
