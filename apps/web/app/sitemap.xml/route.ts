import { RSC_API_BASE } from '@/lib/api-server';
import type { BlogPostListResponse, Category, ProductListResponse } from '@/lib/types';

/**
 * The sitemap, cached at the edge with a hard expiry.
 *
 * **Why it is a route handler and not `app/sitemap.ts`.** A metadata route
 * cannot set its own `Cache-Control`, so its only way to cache is ISR. ISR is
 * stale-while-revalidate, and it only moves when a request arrives. The old
 * sitemap also read its fetches from Next's data cache, which is a second
 * stale-while-revalidate layer. So a crawler's visit got the previous copy and
 * started a rebuild in the background, and that rebuild read a product list the
 * data cache had also kept from before. A sitemap fetched a few times a day
 * therefore described the catalogue as it was one or two visits earlier. On
 * 2026-09-27 it was rebuilt at 18:49 and still listed cakes that sold out at
 * 13:43.
 *
 * **What it does now.** It is built from live data (`no-store`), and the
 * response is cached by the CDN with `s-maxage` and *no*
 * `stale-while-revalidate`. Almost every request is an edge hit. Once the hour
 * is up, the next request waits for a fresh build (a second or two, for a
 * crawler) instead of receiving the old one. Staleness is at most an hour, at
 * any traffic level.
 *
 * **Failure is still a failure.** Every fetch throws, so a broken API gives a
 * 500. A 500 is not cached, and a search engine keeps the last sitemap it read
 * and tries again later. That is F-WEB-6: never cache a partial sitemap as if
 * it were the truth.
 *
 * Mirrors `app/image-sitemap.xml/route.ts`.
 */
export const dynamic = 'force-dynamic';

const BASE = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';
const LOCALES = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? 'en,ar').split(',');

/**
 * Generous on purpose. Every build now calls the API live, and a crawler will
 * wait. The old 5s limit turned a post-deploy latency spike (a blog list at
 * 5.2s while the new container warmed up) into a 500.
 */
const TIMEOUT_MS = 15_000;

/** The hard CDN expiry described above. Mirrors `FEED_TTL`. */
const CACHE_CONTROL = 'public, max-age=0, s-maxage=3600';

type ChangeFrequency = 'daily' | 'weekly' | 'monthly' | 'yearly';

interface Entry {
  path: string;
  lastModified?: string;
  changeFrequency: ChangeFrequency;
  priority: number;
}

const STATIC_PATHS: Entry[] = [
  { path: '', priority: 1.0, changeFrequency: 'weekly' },
  { path: '/about', priority: 0.7, changeFrequency: 'monthly' },
  { path: '/contact', priority: 0.7, changeFrequency: 'monthly' },
  { path: '/faq', priority: 0.6, changeFrequency: 'monthly' },
  { path: '/all-products', priority: 0.8, changeFrequency: 'weekly' },
  { path: '/blog', priority: 0.7, changeFrequency: 'weekly' },
  { path: '/privacy', priority: 0.3, changeFrequency: 'yearly' },
];

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${RSC_API_BASE}${path}`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`sitemap: ${path} returned ${res.status}`);
  return res.json() as Promise<T>;
}

/** Every page of a paginated list. The first page is already in hand. */
async function allPages<T>(
  first: { items: T[]; pages: number },
  pathFor: (page: number) => string,
): Promise<T[]> {
  const rest = await Promise.all(
    Array.from({ length: Math.max(0, first.pages - 1) }, (_, i) =>
      getJson<{ items: T[] }>(pathFor(i + 2)),
    ),
  );
  return [...first.items, ...rest.flatMap((r) => r.items)];
}

// Every product with a page, sold out or not. A cake that sold out tonight is
// a live page marked out of stock, not a 404, so it stays in the sitemap, and
// the sitemap does not change every evening and morning as kitchens sell out.
const productsPath = (page: number) =>
  `/products?per_page=100&page=${page}&is_active=true&include_unavailable=true`;
const blogPath = (page: number) => `/blog/public?locale=en&per_page=50&page=${page}`;

async function entries(): Promise<Entry[]> {
  // In parallel: nothing here depends on anything else.
  const [categories, firstProducts, firstPosts] = await Promise.all([
    getJson<Category[]>('/categories'),
    getJson<ProductListResponse>(productsPath(1)),
    getJson<BlogPostListResponse>(blogPath(1)),
  ]);
  const [products, posts] = await Promise.all([
    allPages(firstProducts, productsPath),
    allPages(firstPosts, blogPath),
  ]);

  return [
    ...STATIC_PATHS,
    ...categories
      .filter((c) => c.is_active)
      .map((c): Entry => ({
        path: `/${c.slug}`,
        lastModified: c.updated_at,
        priority: 0.9,
        changeFrequency: 'daily',
      })),
    ...products
      .filter((p) => p.category)
      .map((p): Entry => ({
        path: `/${p.category!.slug}/${p.slug}`,
        lastModified: p.updated_at,
        priority: 0.8,
        changeFrequency: 'weekly',
      })),
    ...posts.map((post): Entry => ({
      path: `/blog/${post.slug}`,
      lastModified: post.updated_at,
      priority: 0.7,
      changeFrequency: 'weekly',
    })),
  ];
}

function escapeXml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

/** One `<url>` per locale, each naming every locale as an alternate. */
function toXml(list: Entry[]): string {
  const urls = list.flatMap(({ path, lastModified, changeFrequency, priority }) => {
    const alternates = LOCALES.map(
      (l) =>
        `<xhtml:link rel="alternate" hreflang="${l}" href="${escapeXml(`${BASE}/${l}${path}`)}" />`,
    ).join('\n');
    return LOCALES.map((locale) =>
      [
        '<url>',
        `<loc>${escapeXml(`${BASE}/${locale}${path}`)}</loc>`,
        alternates,
        lastModified ? `<lastmod>${escapeXml(lastModified)}</lastmod>` : '',
        `<changefreq>${changeFrequency}</changefreq>`,
        `<priority>${priority}</priority>`,
        '</url>',
      ]
        .filter(Boolean)
        .join('\n'),
    );
  });
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
${urls.join('\n')}
</urlset>
`;
}

export async function GET(): Promise<Response> {
  return new Response(toXml(await entries()), {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
      'Cache-Control': CACHE_CONTROL,
    },
  });
}
