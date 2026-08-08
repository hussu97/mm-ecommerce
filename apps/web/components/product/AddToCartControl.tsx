'use client';

import { useMemo, useState } from 'react';
import { useCart } from '@/lib/cart-context';
import { useToast } from '@/components/ui/Toast';
import { ApiError } from '@/lib/api';
import { analytics } from '@/lib/analytics';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { computeFromPrice } from '@/lib/pricing';
import { ModifierModal } from '@/components/product/ModifierModal';
import type { Product } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

/**
 * The add-to-cart control used on every product tile.
 *
 * Once something is in the basket the tile shows how many, and lets that number
 * be changed in place. Before this the button said "Add" forever: a second tap
 * silently added a second box, and there was no way to tell from the grid what
 * was already in the basket or to take any of it back out.
 *
 * Products with modifiers behave differently on purpose. "2 in basket" there
 * may be a three-piece box and a nine-piece box, and a minus button cannot know
 * which one is meant, so those tiles show the count and send further changes to
 * the options modal or the cart page.
 */
export function AddToCartControl({
  product,
  size = 'default',
}: {
  product: Product;
  size?: 'default' | 'compact';
}) {
  const { t, locale } = useTranslation();
  const { cart, addItem, updateItem, removeItem } = useCart();
  const { addToast } = useToast();
  const [busy, setBusy] = useState(false);
  const [showModal, setShowModal] = useState(false);

  const hasModifiers = Boolean(product.product_modifiers && product.product_modifiers.length > 0);
  const isOutOfStock = product.is_stock_product && product.stock_quantity <= 0;
  const productName = localizedField(product, 'name', product.name, locale);

  const lines = useMemo(
    () => (cart?.items ?? []).filter((i) => i.product_id === product.id),
    [cart, product.id],
  );
  const inCart = lines.reduce((sum, i) => sum + i.quantity, 0);
  const simpleLine = !hasModifiers ? lines[0] : undefined;

  const compact = size === 'compact';
  // The default size sits in the listing grid, which is two-up on phones: the
  // label has roughly half a screen to live in there, so it steps down until sm.
  const btn = compact
    ? 'text-[10px] px-3 py-1.5 tracking-widest'
    : 'text-[10px] sm:text-xs px-2 py-2 sm:py-2.5 tracking-wider sm:tracking-widest';

  const add = async () => {
    setBusy(true);
    try {
      await addItem(product.id, 1, []);
      analytics.addToCart({
        product_name: product.name,
        variant_name: '',
        price: computeFromPrice(product),
        quantity: 1,
      });
      addToast(t('product.added_to_cart', { name: productName }), 'success');
    } catch (err) {
      addToast(err instanceof ApiError ? err.message : t('product.failed_to_add'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const step = async (next: number) => {
    if (!simpleLine) return;
    setBusy(true);
    try {
      if (next <= 0) {
        await removeItem(simpleLine.id);
        analytics.removeFromCart({ product_name: product.name });
      } else {
        await updateItem(simpleLine.id, next);
      }
    } catch (err) {
      addToast(err instanceof ApiError ? err.message : t('cart.failed_update'), 'error');
    } finally {
      setBusy(false);
    }
  };

  if (isOutOfStock) {
    return (
      <button
        disabled
        className={`w-full bg-gray-100 text-gray-400 font-body uppercase cursor-not-allowed ${btn}`}
      >
        {t('product.out_of_stock')}
      </button>
    );
  }

  // ── Products priced through options ────────────────────────────────────────
  if (hasModifiers) {
    return (
      <>
        <div className="flex items-center gap-2">
          {inCart > 0 && (
            <span
              className="shrink-0 inline-flex items-center gap-1 px-1.5 sm:px-2 py-1 bg-primary/10 text-primary font-body text-[11px] rounded-sm"
              aria-label={t('product.in_basket', { n: inCart })}
            >
              <Icon name="shopping_basket" className="text-sm" />
              {inCart}
            </span>
          )}
          <button
            onClick={() => setShowModal(true)}
            className={`flex-1 bg-primary text-white font-body uppercase hover:opacity-90 transition-opacity ${btn}`}
          >
            {inCart > 0 ? t('product.add_more') : t('product.select_options')}
          </button>
        </div>
        {showModal && <ModifierModal product={product} onClose={() => setShowModal(false)} />}
      </>
    );
  }

  // ── Plain products: the count is editable in place ─────────────────────────
  if (inCart > 0) {
    return (
      <div className="flex items-center justify-between gap-2 border border-primary rounded-sm">
        <button
          onClick={() => step(inCart - 1)}
          disabled={busy}
          aria-label={inCart === 1 ? t('cart.remove_item') : t('product.decrease')}
          className="w-8 h-8 sm:w-9 sm:h-9 flex items-center justify-center text-primary hover:bg-primary/10 transition-colors disabled:opacity-40"
        >
          <Icon name={inCart === 1 ? 'delete_outline' : 'remove'} className="text-base" />
        </button>
        <span className="font-body text-sm text-primary tabular-nums" aria-live="polite">
          {inCart}
        </span>
        <button
          onClick={() => step(inCart + 1)}
          disabled={busy}
          aria-label={t('product.increase')}
          className="w-8 h-8 sm:w-9 sm:h-9 flex items-center justify-center text-primary hover:bg-primary/10 transition-colors disabled:opacity-40"
        >
          <Icon name="add" className="text-base" />
        </button>
      </div>
    );
  }

  return (
    <button
      onClick={add}
      disabled={busy}
      className={`w-full bg-primary text-white font-body uppercase hover:opacity-90 transition-opacity disabled:opacity-50 ${btn}`}
    >
      {busy ? t('product.adding') : t('product.add_short')}
    </button>
  );
}
