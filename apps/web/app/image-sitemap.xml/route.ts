import { API_BASE } from '@/lib/api';
import type { ProductListResponse, Category } from '@/lib/types';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export async function GET() {
  let urls = '';

  try {
    // Fetch categories
    const catRes = await fetch(`${API_BASE}/categories`, {
      next: { revalidate: 3600 },
      signal: AbortSignal.timeout(5000),
    });

    if (catRes.ok) {
      const categories: Category[] = await catRes.json();
      for (const c of categories.filter(cat => cat.is_active && cat.image_url)) {
        const imgProxy = `${SITE_URL}/_next/image?url=${encodeURIComponent(c.image_url!)}&w=1200&q=75`;
        urls += `  <url>
    <loc>${SITE_URL}/en/${c.slug}</loc>
    <image:image>
      <image:loc>${escapeXml(imgProxy)}</image:loc>
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
        `${API_BASE}/products?per_page=100&page=${page}&is_active=true`,
        { next: { revalidate: 3600 }, signal: AbortSignal.timeout(5000) },
      );
      if (!res.ok) break;

      const data: ProductListResponse = await res.json();
      for (const p of data.items) {
        if (!p.category || !p.image_urls?.length) continue;
        const loc = `${SITE_URL}/en/${p.category.slug}/${p.slug}`;
        const images = p.image_urls
          .map(
            url =>
              `    <image:image>
      <image:loc>${escapeXml(url)}</image:loc>
      <image:title>${escapeXml(p.name)}</image:title>
    </image:image>`,
          )
          .join('\n');
        urls += `  <url>\n    <loc>${loc}</loc>\n${images}\n  </url>\n`;
      }

      hasMore = page < data.pages;
      page++;
    }
  } catch {
    // Return whatever we collected so far
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
