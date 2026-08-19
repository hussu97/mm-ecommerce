import { NextResponse } from 'next/server';

import { getFeaturedPromo, offerSentence } from '@/lib/offer';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export const revalidate = 86400; // 24 hours

export async function GET() {
  // Read from the coupon row rather than written into the paragraph below.
  // This manifest is cached for a day and is read by models rather than by
  // people, so a figure typed in by hand here is a wrong figure repeated for a
  // day at a time with nobody to notice — which is exactly what happened: it
  // claimed 15% and no code while the live coupon was 20% with the code NEW.
  const offer = offerSentence(await getFeaturedPromo());

  const plugin = {
    schema_version: 'v1',
    name_for_human: 'Melting Moments Cakes',
    name_for_model: 'melting_moments_cakes',
    description_for_human:
      'Home bakery in Sharjah, UAE. Brownies, cookies, cookie melts, cakes and desserts, delivered to every emirate.',
    description_for_model:
      `Melting Moments Cakes is a bakery in Sharjah, UAE, founded and run by Fatema Abbasi. It makes brownies, cookies, cookie melts, mix boxes, cakes and other desserts, all baked to order, and delivers to all seven emirates (Dubai, Sharjah, Ajman, Abu Dhabi, Al Ain, Fujairah, Ras Al Khaimah, Umm Al Quwain). Orders go through the website or WhatsApp (+971 50 368 7757). Price range AED 15–200. Payment is by card online (Visa, Mastercard, Apple Pay); cash is accepted on pickup orders only, and there is no cash on delivery. Delivery is priced from the address: free anywhere in Sharjah city and delivered in about an hour; AED 20 in Dubai and Ajman, free over AED 75, same day; AED 30 in Umm Al Quwain and AED 50 in Ras Al Khaimah; AED 80 elsewhere in the UAE, free over AED 200, next day. Orders of AED 35 or less carry a AED 15 small-order fee, which does not apply above that or to pickup.${offer ? ` ${offer}` : ''} Everything is halal, and there is an eggless range. Hours: Monday–Saturday 08:00–23:30, Sunday 15:00–23:30 (UAE time). Full product catalogue at the endpoint below.`,
    auth: {
      type: 'none',
    },
    api: {
      type: 'openapi',
      url: `${SITE_URL}/api/openapi.json`,
      is_user_authenticated: false,
    },
    logo_url: `${SITE_URL}/images/logos/color_logo.jpeg`,
    contact_email: 'hello@meltingmomentscakes.com',
    legal_info_url: `${SITE_URL}/en/privacy`,
    llms_txt: `${SITE_URL}/llms-full.txt`,
  };

  return NextResponse.json(plugin, {
    headers: {
      'Cache-Control': 'public, max-age=86400',
    },
  });
}
