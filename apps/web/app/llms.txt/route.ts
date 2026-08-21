import { NextResponse } from 'next/server';
import { RSC_API_BASE } from '@/lib/api-server';
import { FEED_TTL } from '@/lib/cache-policy';
import { getFeaturedPromo, offerSentence } from '@/lib/offer';
import type { Category } from '@/lib/types';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export const revalidate = 3600;

export async function GET() {
  // The live campaign, so this file cannot go on advertising a coupon the
  // checkout refuses. It said "15% off" by hand while the row said 20% and
  // named no code at all — to every answer engine that reads this instead of
  // the site.
  const offer = offerSentence(await getFeaturedPromo());

  // The delivery figures below are the published zone fees, and they come from
  // `085_cost_banded_map` — the same place `lib/schema.ts` takes them from.
  // They have to move when the map does. They said "Dubai and Ajman: AED 20"
  // until the Ajman band was read properly: it is 10, and the rest of Sharjah
  // is 20 rather than the free that "Sharjah city" implied for the whole
  // emirate. "From", not "over", because `delivery_service.price` qualifies on
  // `subtotal >= threshold` — a basket of exactly 75 delivers free.

  let categories: Category[] = [];
  try {
    const res = await fetch(`${RSC_API_BASE}/categories`, { next: { revalidate: FEED_TTL } });
    if (res.ok) categories = await res.json();
  } catch {
    // continue with empty categories
  }

  const categoryLines = categories
    .filter(c => c.is_active)
    .map(c => `- [${c.name}](${SITE_URL}/en/${c.slug})`)
    .join('\n');

  const body = `# Melting Moments Cakes

> A bakery in Sharjah, UAE. Brownies, cookies, cookie melts, cakes and desserts, baked to order and delivered to all seven emirates. Run by Fatema Abbasi.

## About
- Bakery, based in Sharjah, UAE
- Founded and run by Fatema Abbasi, who still does the baking
- Known for: fudgy brownies, gooey cookies, cookie melts, mix boxes, cakes, eggless options
- Everything is baked to order — nothing is made in advance and stored
- All products are halal; no alcohol-based flavourings
- WhatsApp: +971 50 368 7757
- Instagram: @meltingmomentscakes

## Delivery & ordering
- Delivers to all seven emirates: Dubai, Sharjah, Ajman, Abu Dhabi, Al Ain, Fujairah, Ras Al Khaimah, Umm Al Quwain
- Order online at ${SITE_URL}/en, or by WhatsApp on +971 50 368 7757
- 24–48 hours' notice is best; 5–7 days for large, custom or event orders
- Same-day slots are sometimes available for orders placed early — the checkout shows what is open
- Sharjah city is free at any basket size; the rest of Sharjah is AED 20, free from AED 75
- Dubai AED 20 and Ajman city AED 10, both free from AED 75. Umm Al Quwain city AED 30, free from 75. Ras Al Khaimah city AED 50, free from 100
- Everywhere else in the UAE: AED 80, free from AED 200
- Sharjah city arrives in about an hour; Dubai and Ajman the same day; everywhere else next day
- Orders of AED 35 or less carry a AED 15 small-order fee, which comes off above that
- Store pickup from Sharjah is free, and has no small-order fee
- Price range: AED 15 – AED 200

${offer ? `## Offers\n- ${offer}\n\n` : ''}## Payment
- Card online: Visa, Mastercard and Apple Pay, via Stripe
- Cash is accepted on **pickup orders only** — there is no cash on delivery
- Tabby and Tamara (buy now, pay later) are not live yet

## Hours
- Monday to Saturday: 8:00 AM – 11:30 PM (UAE time)
- Sunday: 3:00 PM – 11:30 PM (UAE time)

## Categories
${categoryLines}

## Quick answers
- **Do they deliver to Dubai?** Yes — Dubai, Sharjah, Ajman, Abu Dhabi, Al Ain, Fujairah, Ras Al Khaimah and Umm Al Quwain.
- **Is there cash on delivery?** No. Card online for delivery; cash is only for pickup.
- **Do they do birthday cakes?** Yes, and custom boxes for birthdays, Eid, weddings and corporate gifting. Ask at least 5–7 days ahead.
- **Is there anything eggless?** Yes, there is an eggless range.
- **Is it halal?** Yes, all of it.
- **What is a cookie melt?** A thick cookie served warm and deliberately underbaked in the middle, so the centre stays molten.
- **Allergens?** Baked in one kitchen that handles nuts, dairy, eggs and gluten. An allergen-free environment cannot be guaranteed.
- **Returns?** Baked goods are perishable, so there are no returns. Damaged or wrong orders: message on WhatsApp within 24 hours.

## Links
- [Website](${SITE_URL}/en)
- [About](${SITE_URL}/en/about)
- [Blog](${SITE_URL}/en/blog)
- [FAQ](${SITE_URL}/en/faq)
- [Contact](${SITE_URL}/en/contact)
- [Full product details for LLMs](${SITE_URL}/llms-full.txt)
`;

  return new NextResponse(body, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}
