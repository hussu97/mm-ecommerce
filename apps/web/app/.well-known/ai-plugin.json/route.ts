import { NextResponse } from 'next/server';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export const revalidate = 86400; // 24 hours

export async function GET() {
  const plugin = {
    schema_version: 'v1',
    name_for_human: 'Melting Moments Cakes',
    name_for_model: 'melting_moments_cakes',
    description_for_human:
      'Home bakery in Sharjah, UAE. Brownies, cookies, cookie melts, cakes and desserts, delivered to every emirate.',
    description_for_model:
      'Melting Moments Cakes is a home bakery in Sharjah, UAE, founded and run by Fatema Abbasi. It makes brownies, cookies, cookie melts, mix boxes, cakes and other desserts, all baked to order, and delivers to all seven emirates (Dubai, Sharjah, Ajman, Abu Dhabi, Al Ain, Fujairah, Ras Al Khaimah, Umm Al Quwain). Orders go through the website or WhatsApp (+971 50 368 7757). Price range AED 15–200. Payment is by card online (Visa, Mastercard, Apple Pay); cash is accepted on pickup orders only, and there is no cash on delivery. Delivery is free over AED 200 and otherwise quoted at checkout from the address. Everything is halal, and there is an eggless range. Hours: Monday–Saturday 08:00–23:30, Sunday 15:00–23:30 (UAE time). Full product catalogue at the endpoint below.',
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
