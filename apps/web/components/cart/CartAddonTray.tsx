'use client';

import Image from 'next/image';
import { useEffect, useState } from 'react';
import { productsApi } from '@/lib/api';
import { formatPrice } from '@/lib/utils';
import { analytics, failureReason } from '@/lib/analytics';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { Icon } from '@/components/ui/Icon';
import type { Product } from '@/lib/types';

const PLACEHOLDER_IMAGE = '/images/logos/main_logo.png';

interface CartAddonTrayProps {
  /** Product ids already in the basket — those are not offered again. */
  inCart: Set<string>;
  /** Adds one. Resolves to an error message to show, or null on success. */
  onAdd: (product: Product) => Promise<string | null>;
}

/**
 * The small extras the basket offers — a handwritten note today.
 *
 * It is a horizontal strip rather than a grid on purpose: this is a suggestion
 * nobody asked for, sitting on the page whose only job is to get to checkout,
 * and a block of tiles competes with the button. One row that scrolls stays out
 * of the way on a phone and reads as an afterthought, which is what it is.
 *
 * Nothing renders while the request is in flight, on failure, or once every
 * add-on is already in the basket. A tray is worth having only when it has
 * something to offer; a spinner or an empty box in this position is pure noise
 * between the basket and the checkout button.
 */
export function CartAddonTray({ inCart, onAdd }: CartAddonTrayProps) {
  const { t, locale } = useTranslation();
  const [addons, setAddons] = useState<Product[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    productsApi
      .cartAddons()
      .then((list) => { if (!cancelled) setAddons(list); })
      .catch(() => { /* no add-ons we can prove are sellable, so no tray */ });
    return () => { cancelled = true; };
  }, []);

  const offered = addons.filter((p) => !inCart.has(p.id));
  if (offered.length === 0) return null;

  async function handleAdd(product: Product) {
    setBusyId(product.id);
    setError(null);
    try {
      const message = await onAdd(product);
      if (message) {
        setError(message);
        analytics.cartActionFailed({ action: 'add', reason: failureReason(message), surface: 'cart' });
        return;
      }
      analytics.cartAddonAdded({
        product_name: product.name,
        price: product.base_price,
        personalised: Boolean(product.personalisation_type),
      });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="mt-6 pt-6 border-t border-gray-100" aria-label={t('cart.addons_title')}>
      <h2 className="font-display text-sm text-primary uppercase tracking-widest">
        {t('cart.addons_title')}
      </h2>
      <p className="font-body text-xs text-gray-500 mt-1">{t('cart.addons_subtitle')}</p>

      {error && <p className="font-body text-xs text-red-500 mt-2">{error}</p>}

      {/*
        `-mx-4 px-4` lets the row bleed to the screen edge on a phone so the
        last card is visibly cut off — the only reliable cue that a strip
        scrolls, short of drawing arrows.
      */}
      <ul className="flex gap-3 overflow-x-auto mt-3 -mx-4 px-4 sm:mx-0 sm:px-0 pb-1 snap-x">
        {offered.map((product) => {
          const image = product.image_urls?.[0] ?? PLACEHOLDER_IMAGE;
          const busy = busyId === product.id;
          return (
            <li
              key={product.id}
              className="shrink-0 w-40 snap-start border border-gray-100 rounded-sm p-3 flex flex-col gap-2"
            >
              <div className="relative w-full h-20 rounded-sm overflow-hidden bg-gray-50">
                <Image
                  src={image}
                  alt=""
                  fill
                  sizes="160px"
                  className="object-cover"
                  onError={(e) => { (e.currentTarget as HTMLImageElement).src = PLACEHOLDER_IMAGE; }}
                />
              </div>
              <p className="font-body text-xs font-medium text-gray-900 leading-snug line-clamp-2">
                {localizedField(product, 'name', product.name, locale)}
              </p>
              <p className="font-body text-xs text-gray-500 mt-auto" dir="ltr">
                {formatPrice(product.base_price, locale)}
              </p>
              <button
                onClick={() => handleAdd(product)}
                disabled={busy}
                className="flex items-center justify-center gap-1 rounded-sm border border-primary/30 py-1.5
                  font-body text-xs uppercase tracking-wider text-primary
                  hover:bg-primary hover:text-white transition-colors disabled:opacity-50"
              >
                <Icon name="add" className="text-sm" />
                {busy ? t('cart.addon_adding') : t('cart.addon_add')}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
