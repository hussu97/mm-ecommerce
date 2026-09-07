import type { Product } from '@/lib/types';

/**
 * Lowest price a customer can actually pay for a product — what we show as
 * "From X AED" on cards and product pages, and what every `Offer.price` in
 * this app's JSON-LD publishes. This is the **only** place that computation
 * happens; nothing else in `apps/web` may recompute it.
 *
 * Starts at base_price and adds the cheapest option of every *required*
 * modifier group. Many products are priced entirely through their modifiers
 * (base_price = 0, e.g. "Brown Butter Cookies" with 3/6/9-piece options), so
 * when the running total is still 0 we fall back to the cheapest priced
 * option across *every* group, active or not required. Without that fallback
 * the card reads "From 0.00 AED", which reads as broken and costs the sale.
 *
 * The fallback takes the **global** minimum, not the first group's — an
 * earlier version returned the first group with a non-zero cheapest option,
 * so a product whose first modifier group happened to be the pricier one
 * quoted the wrong floor. Mirrors `_from_price()` in
 * `apps/api/app/services/catalog/product_service.py`, which sorts the
 * catalogue on the same number and has always taken the true global minimum.
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
    const cheapestPerGroup: number[] = [];
    for (const pm of product.product_modifiers ?? []) {
      const priced = pm.modifier.options.filter((o) => o.is_active && Number(o.price) > 0);
      if (priced.length === 0) continue;
      cheapestPerGroup.push(Math.min(...priced.map((o) => Number(o.price))));
    }
    if (cheapestPerGroup.length > 0) {
      price = Math.min(...cheapestPerGroup);
    }
  }

  return price;
}

/**
 * `computeFromPrice`, guarded for the surfaces that must never publish `0`:
 * a JSON-LD `Offer.price` of "0.00" is read by Google Merchant Center and by
 * answer engines that repeat it verbatim, and a genuinely unpriced product
 * (no base price, no priced modifiers) has nothing honest to say there.
 *
 * Returns `null` rather than `0` in that case, which the two JSON-LD builders
 * that call this drop the `Offer` for entirely — a listing with no price is a
 * smaller wrong than a listing that claims to be free.
 */
export function offerPrice(product: Product): number | null {
  const price = computeFromPrice(product);
  return price > 0 ? price : null;
}

/**
 * True when the product carries no standalone base price and is priced purely
 * through its modifier options. Those options are the whole price, not a
 * surcharge, so the UI must render them as "40.00 AED" rather than "+40.00 AED".
 */
export function isModifierPriced(product: Product): boolean {
  return Number(product.base_price) === 0 && (product.product_modifiers?.length ?? 0) > 0;
}
