import { RSC_API_BASE } from '@/lib/api-server';
import { FEED_TTL } from '@/lib/cache-policy';
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
export async function GET() {
  let urls = '';

  // Fetch categories
  const catRes = await fetch(`${RSC_API_BASE}/categories`, {
    next: { revalidate: FEED_TTL },
    signal: AbortSignal.timeout(5000),
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

  // Fetch all products (paginated)
  let page = 1;
  let hasMore = true;
  while (hasMore) {
    const res = await fetch(
      `${RSC_API_BASE}/products?per_page=100&page=${page}&is_active=true`,
      { next: { revalidate: FEED_TTL }, signal: AbortSignal.timeout(5000) },
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
      'Cache-Control': 'public, max-age=3600, s-maxage=3600',
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
