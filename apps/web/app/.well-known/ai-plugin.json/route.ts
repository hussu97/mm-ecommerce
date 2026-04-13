import { NextResponse } from 'next/server';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export const revalidate = 86400; // 24 hours

export async function GET() {
  const plugin = {
    schema_version: 'v1',
    name_for_human: 'Melting Moments Cakes',
    name_for_model: 'melting_moments_cakes',
    description_for_human:
      'Artisanal bakery in the UAE. Handcrafted brownies, cookies, cookie melts, and desserts delivered across all Emirates.',
    description_for_model:
      'Melting Moments Cakes is an artisanal home-based bakery in Sharjah, UAE, founded by Fatema Abbasi. They make handcrafted brownies, cookies, cookie melts, mix boxes, and other desserts, and deliver to all UAE Emirates. Orders can be placed on the website or via WhatsApp (+971 50 368 7757). Price range AED 15–200. Payment by cash on delivery or card. Monday–Saturday 08:00–23:30, Sunday 15:00–23:30 (UAE time). Full product catalogue available at the api endpoint below.',
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
