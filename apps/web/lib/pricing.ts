import type { Product } from '@/lib/types';

/**
 * Lowest price a customer can actually pay for a product — what we show as
 * "From X AED" on cards and product pages.
 *
 * Starts at base_price and adds the cheapest option of every *required*
 * modifier group. Many products are priced entirely through their modifiers
 * (base_price = 0, e.g. "Brown Butter Cookies" with 3/6/9-piece options), so
 * when the running total is still 0 we fall back to the cheapest option on any
 * group. Without that fallback the card reads "From 0.00 AED", which reads as
 * broken and costs the sale.
 */
export function computeFromPrice(product: Product): number {
  let price = Number(product.base_price);

  for (const pm of product.product_modifiers ?? []) {
    if (pm.minimum_options <= 0) continue;
    const active = pm.modifier.options.filter((o) => o.is_active);
    if (active.length === 0) continue;
    price += Math.min(...active.map((o) => Number(o.price))) * Math.min(pm.minimum_options, 1);
  }

  if (price === 0 && (product.product_modifiers?.length ?? 0) > 0) {
    for (const pm of product.product_modifiers ?? []) {
      const active = pm.modifier.options.filter((o) => o.is_active);
      if (active.length === 0) continue;
      const cheapest = Math.min(...active.map((o) => Number(o.price)));
      if (cheapest > 0) {
        price = cheapest;
        break;
      }
    }
  }

  return price;
}

/**
 * True when the product carries no standalone base price and is priced purely
 * through its modifier options. Those options are the whole price, not a
 * surcharge, so the UI must render them as "40.00 AED" rather than "+40.00 AED".
 */
export function isModifierPriced(product: Product): boolean {
  return Number(product.base_price) === 0 && (product.product_modifiers?.length ?? 0) > 0;
}
