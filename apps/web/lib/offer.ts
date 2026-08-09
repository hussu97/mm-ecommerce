import { RSC_API_BASE } from '@/lib/api';
import { CACHE_TAGS, CONTENT_TTL } from '@/lib/cache-policy';
import { fetchJson } from '@/lib/fetch-json';
import type { AdvertisedPromo } from '@/lib/types';

/**
 * The advertised new-customer coupon, for everything the server renders.
 *
 * There are four surfaces that state this offer and none of them is a page a
 * customer scrolls: the homepage strip's markup, the homepage's JSON-LD, and
 * the two `llms*.txt` files an answer engine reads instead of the site. All
 * four were written by hand and all four said **15% off, no code** while the
 * coupon in the database was 20% off with the code `NEW` — an offer published
 * to every crawler and every AI answer that the checkout would then refuse.
 *
 * So the figures are fetched, once, here. A campaign edited in the admin
 * changes all of them together, and retiring it removes all of them together.
 *
 * Failure is swallowed and returns `null`, unlike the catalogue fetches. A page
 * that has stopped selling is a fault worth failing a build over; a page
 * missing an advert is a page.
 */
export async function getFeaturedPromo(): Promise<AdvertisedPromo | null> {
  try {
    return await fetchJson<AdvertisedPromo>(`${RSC_API_BASE}/promo-codes/featured`, {
      next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.promo] },
      signal: AbortSignal.timeout(8000),
    });
  } catch {
    return null;
  }
}

/**
 * Can this coupon be stated in the words we have?
 *
 * Our copy everywhere is percentage-shaped ("{percent}% off your first
 * {orders} orders"), so a fixed-amount coupon pushed through it reads as "20%
 * off" for a 20-dirham discount. On screen that is a bad sentence; in
 * `llms.txt` or a JSON-LD `Offer` it is a bad sentence that gets repeated by
 * machines to people who never visited. Saying nothing is the safe failure.
 */
export function isAdvertisable(promo: AdvertisedPromo | null): promo is AdvertisedPromo {
  return (
    promo !== null && promo.discount_type === 'percentage' && promo.first_orders_limit !== null
  );
}

/**
 * One English sentence describing the live offer, or `null` when none is.
 *
 * Written for the machine-readable surfaces — `llms.txt`, `llms-full.txt`, the
 * plugin manifest, the meta description — which are English-only and want prose
 * rather than an i18n key. The customer-facing components render the same facts
 * through `promo.*` translations instead.
 *
 * Names the code, which every hand-written version of this sentence forgot: an
 * answer engine telling somebody about a discount they then cannot find is
 * worse than not mentioning it. Says "delivery orders" about the phone gate,
 * because collection is never asked.
 */
export function offerSentence(promo: AdvertisedPromo | null): string | null {
  if (!isAdvertisable(promo)) return null;
  const percent = Number(promo.discount_value);
  const parts = [
    `New customers get ${percent}% off each of their first ${promo.first_orders_limit} ` +
      `orders with the code ${promo.code}`,
  ];
  if (promo.max_discount_amount !== null) {
    parts.push(`up to AED ${promo.max_discount_amount} off per order`);
  }
  if (promo.requires_phone_verification) {
    parts.push('delivery orders need a verified mobile number');
  }
  return `${parts.join(', ')}. Limited-time promotion.`;
}
