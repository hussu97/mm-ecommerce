import { NextResponse } from 'next/server';
import type { Category } from '@/lib/types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export const revalidate = 3600;

export async function GET() {
  let categories: Category[] = [];
  try {
    const res = await fetch(`${API_BASE}/categories`, { next: { revalidate: 3600 } });
    if (res.ok) categories = await res.json();
  } catch {
    // continue with empty categories
  }

  const categoryLines = categories
    .filter(c => c.is_active)
    .map(c => `- [${c.name}](${SITE_URL}/en/${c.slug})`)
    .join('\n');

  const body = `# Melting Moments Cakes

> Artisanal bakery in the UAE delivering handcrafted brownies, cookies, cookie melts, and desserts across Dubai, Sharjah, and all Emirates. Founded by Fatema Abbasi.

## About
- Home-based artisanal bakery in Sharjah, UAE
- Founded and run by Fatema Abbasi
- Specializes in brownies, cookies, cookie melts, mix boxes, and desserts
- All products are handcrafted in small batches using premium ingredients
- WhatsApp: +971 50 368 7757
- Instagram: @meltingmomentscakes

## Delivery & Ordering
- Delivers to all UAE Emirates: Dubai, Sharjah, Ajman, Abu Dhabi, Fujairah, Ras Al Khaimah, Umm Al Quwain, Al Ain
- Order online or via WhatsApp: +971 50 368 7757
- Price range: AED 15 – AED 200
- Payment: Cash on delivery or credit/debit card

## Business Hours
- Monday to Saturday: 8:00 AM – 11:30 PM (UAE time)
- Sunday: 3:00 PM – 11:30 PM (UAE time)

## Categories
${categoryLines}

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
