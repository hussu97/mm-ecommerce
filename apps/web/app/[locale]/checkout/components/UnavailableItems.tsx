'use client';

import { useState } from 'react';

import { withFallback } from '@/lib/i18n/fallback';
import { useCart } from '@/lib/cart-context';
import type { Cart, UnavailableItem } from '@/lib/types';

type TFunction = (key: string, params?: Record<string, string | number>) => string;

/**
 * The lines the branch serving this address cannot make, and how to fix them.
 *
 * This exists because the catalogue cannot warn anybody. A shopper browsing has
 * given us no address, so there is no branch, and a product is hidden from the
 * listing only when *every* branch is out of it. The specific shop is not known
 * until the pin resolves to a delivery zone — which is also the moment a
 * customer editing their address at the checkout can move the whole order to a
 * different kitchen after filling the basket from another one's menu.
 *
 * So the honest place to say it is here, and the honest thing to say is which
 * line and why. `POST /orders` refuses the same basket regardless; without this
 * panel that refusal is a sentence at the pay button naming none of the eight
 * cakes it is about.
 *
 * Removing is the only action offered. Swapping a filling means re-opening the
 * product's modifier sheet with different choices, which is the product page's
 * job and not something to rebuild inside a checkout — so the reason names the
 * filling that ran out and the customer decides.
 */
export function UnavailableItems({
  items,
  cart,
  t,
}: {
  items: UnavailableItem[];
  cart: Cart | null;
  t: TFunction;
}) {
  const { removeItem } = useCart();
  const [removing, setRemoving] = useState<string | null>(null);

  if (items.length === 0) return null;

  /**
   * The basket lines for one unavailable product.
   *
   * Matched by product rather than by cart line because the server answers
   * about products — the same cake added twice with different messages on it is
   * two lines and one problem, and both have to go.
   */
  const linesFor = (productId: string) =>
    (cart?.items ?? []).filter((line) => line.product_id === productId);

  const remove = async (productId: string) => {
    setRemoving(productId);
    try {
      for (const line of linesFor(productId)) {
        await removeItem(line.id);
      }
    } finally {
      setRemoving(null);
    }
  };

  return (
    <section
      // The gate scrolls here when the button is pressed — see
      // `checkout-gate.ts`, `items_unavailable`.
      data-field="unavailableItems"
      className="rounded-xl border border-red-200 bg-red-50 p-4 space-y-3"
      aria-live="polite"
    >
      <div>
        <h3 className="font-heading text-base text-red-900">
          {items.length === 1
            ? withFallback(t, 'checkout.item_unavailable_title', 'One item isn’t available')
            : withFallback(
                t,
                'checkout.items_unavailable_title',
                `${items.length} items aren’t available`,
              )}
        </h3>
        <p className="font-body text-sm text-red-800/80 mt-1">
          {withFallback(
            t,
            'checkout.items_unavailable_body',
            'The shop serving this address has run out. Remove them to carry on, or change your address.',
          )}
        </p>
      </div>

      <ul className="space-y-2">
        {items.map((item) => (
          <li
            key={item.product_id}
            className="flex items-start justify-between gap-3 rounded-lg bg-white/70 p-3"
          >
            <div className="min-w-0">
              <p className="font-body text-sm text-gray-900 truncate">
                {item.product_name}
              </p>
              {/* The server's sentence, verbatim. It knows whether a filling
                  ran out or the whole cake is off, and those read differently
                  to somebody deciding what to do about it. */}
              <p className="font-body text-xs text-red-800/80 mt-0.5">
                {item.reason}
              </p>
            </div>
            <button
              type="button"
              onClick={() => void remove(item.product_id)}
              disabled={removing === item.product_id}
              className="shrink-0 rounded-lg border border-red-300 px-3 py-1.5 font-body text-xs text-red-900 hover:bg-red-100 disabled:opacity-50"
            >
              {removing === item.product_id
                ? withFallback(t, 'checkout.removing', 'Removing…')
                : withFallback(t, 'common.remove', 'Remove')}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
