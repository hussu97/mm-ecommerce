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
 * One sentence describing the live offer, or `null` when none is.
 *
 * Written for the surfaces that want prose rather than an i18n key: the
 * `llms*.txt` files, the plugin manifest, the meta description and the JSON-LD
 * `Offer`. The customer-facing components render the same facts through the
 * `promo.*` translations instead.
 *
 * Names the code, which every hand-written version of this sentence forgot: an
 * answer engine telling somebody about a discount they then cannot find is
 * worse than not mentioning it. Says "delivery orders" about the phone gate,
 * because a collection is never asked.
 *
 * **Locale is not decorative here.** The machine-readable files are English by
 * construction and default to it, but the meta description is per-locale, and
 * an English clause appended to Arabic prose is what a search engine prints as
 * the Arabic snippet — which is exactly what shipped before this argument
 * existed. Copy is inlined rather than read from the `promo.*` translations
 * because these callers are `generateMetadata` and route handlers with no
 * translation context, and a fetch per render to build one sentence is a worse
 * trade than two strings kept beside each other.
 */
export function offerSentence(
  promo: AdvertisedPromo | null,
  locale: 'en' | 'ar' = 'en',
): string | null {
  if (!isAdvertisable(promo)) return null;
  const percent = Number(promo.discount_value);
  const orders = promo.first_orders_limit;
  const cap = promo.max_discount_amount;
  const gated = promo.requires_phone_verification;

  if (locale === 'ar') {
    const parts = [
      `العملاء الجدد يحصلون على خصم ${percent}% على أول ${orders} طلبات باستخدام كود ${promo.code}`,
    ];
    if (cap !== null) parts.push(`خصم يصل إلى ${cap} درهم لكل طلب`);
    if (gated) parts.push('طلبات التوصيل تتطلب رقم هاتف متحقق منه');
    return `${parts.join('، ')}. عرض لفترة محدودة.`;
  }

  const parts = [
    `New customers get ${percent}% off each of their first ${orders} ` +
      `orders with the code ${promo.code}`,
  ];
  if (cap !== null) parts.push(`up to AED ${cap} off per order`);
  if (gated) parts.push('delivery orders need a verified mobile number');
  return `${parts.join(', ')}. Limited-time promotion.`;
}

/** The offer as a short headline — the JSON-LD `Offer.name`. */
export function offerHeadline(
  promo: AdvertisedPromo | null,
  locale: 'en' | 'ar' = 'en',
): string | null {
  if (!isAdvertisable(promo)) return null;
  const percent = Number(promo.discount_value);
  return locale === 'ar'
    ? `خصم ${percent}% على أول ${promo.first_orders_limit} طلبات`
    : `${percent}% off your first ${promo.first_orders_limit} orders`;
}
