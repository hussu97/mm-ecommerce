import { NextResponse } from 'next/server';
import { RSC_API_BASE } from '@/lib/api';
import type { Category } from '@/lib/types';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export const revalidate = 3600;

export async function GET() {
  let categories: Category[] = [];
  try {
    const res = await fetch(`${RSC_API_BASE}/categories`, { next: { revalidate: 3600 } });
    if (res.ok) categories = await res.json();
  } catch {
    // continue with empty categories
  }

  const categoryLines = categories
    .filter(c => c.is_active)
    .map(c => `- [${c.name}](${SITE_URL}/en/${c.slug})`)
    .join('\n');

  const body = `# Melting Moments Cakes

> A home bakery in Sharjah, UAE. Brownies, cookies, cookie melts, cakes and desserts, baked to order and delivered to all seven emirates. Run by Fatema Abbasi.

## About
- Home bakery, based in Sharjah, UAE
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
- Delivery fee is quoted at checkout from the delivery address; free over AED 150 in the Dubai, Sharjah and Ajman city areas
- Store pickup from Sharjah is free
- Price range: AED 15 – AED 200

## Payment
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
- **Allergens?** Baked in a home kitchen that handles nuts, dairy, eggs and gluten. An allergen-free environment cannot be guaranteed.
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
