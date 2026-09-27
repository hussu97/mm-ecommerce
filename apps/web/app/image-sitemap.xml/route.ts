import { RSC_API_BASE } from '@/lib/api-server';
import type { ProductListResponse, Category } from '@/lib/types';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';
const LOCALES = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? 'en,ar').split(',');

// No try/catch, and every fetch throws on a non-2xx or timeout. This route is
// CDN-cached (s-maxage below), so swallowing a failure to "return whatever we
// collected" cached a truncated or empty image sitemap — the same trap the page
// sitemap had (F-WEB-6). Throwing 500s the route instead, which is not cached
// with the success headers, so the CDN keeps serving the last good XML.
// Both locales are emitted: the Arabic pages carry the same product imagery and
// were absent entirely before.
//
// Built from live data and cached by the CDN with a hard hour (`s-maxage`, no
// stale-while-revalidate). It used to read its fetches from Next's data cache,
// which only refreshes when something asks, so a rebuild could start from a
// product list that was already hours old. `app/sitemap.xml/route.ts` explains
// this at length.
export const dynamic = 'force-dynamic';

// Every build calls the API live, and a crawler will wait. See the sitemap.
const TIMEOUT_MS = 15_000;

export async function GET() {
  let urls = '';

  // Fetch categories
  const catRes = await fetch(`${RSC_API_BASE}/categories`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!catRes.ok) throw new Error(`image-sitemap: /categories returned ${catRes.status}`);

  const categories: Category[] = await catRes.json();
  for (const c of categories.filter(cat => cat.is_active && cat.image_url)) {
    for (const locale of LOCALES) {
      urls += `  <url>
    <loc>${SITE_URL}/${locale}/${c.slug}</loc>
    <image:image>
      <image:loc>${escapeXml(c.image_url!)}</image:loc>
      <image:title>${escapeXml(c.name)}</image:title>
    </image:image>
  </url>\n`;
    }
  }

  // Every product with a page, sold out or not — see `app/sitemap.ts`.
  let page = 1;
  let hasMore = true;
  while (hasMore) {
    const res = await fetch(
      `${RSC_API_BASE}/products?per_page=100&page=${page}&is_active=true&include_unavailable=true`,
      { cache: 'no-store', signal: AbortSignal.timeout(TIMEOUT_MS) },
    );
    if (!res.ok) {
      throw new Error(`image-sitemap: /products page ${page} returned ${res.status}`);
    }

    const data: ProductListResponse = await res.json();
    for (const p of data.items) {
      if (!p.category || !p.image_urls?.length) continue;
      const images = p.image_urls
        .map(
          url =>
            `    <image:image>
      <image:loc>${escapeXml(url)}</image:loc>
      <image:title>${escapeXml(p.name)}</image:title>
    </image:image>`,
        )
        .join('\n');
      for (const locale of LOCALES) {
        const loc = `${SITE_URL}/${locale}/${p.category.slug}/${p.slug}`;
        urls += `  <url>\n    <loc>${loc}</loc>\n${images}\n  </url>\n`;
      }
    }

    hasMore = page < data.pages;
    page++;
  }

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
${urls}</urlset>`;

  return new Response(xml, {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
      'Cache-Control': 'public, max-age=0, s-maxage=3600',
    },
  });
}

function escapeXml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}
