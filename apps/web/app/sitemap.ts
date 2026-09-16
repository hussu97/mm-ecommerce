import type { MetadataRoute } from 'next';

import { RSC_API_BASE } from '@/lib/api-server';
import { FEED_TTL } from '@/lib/cache-policy';
import type { BlogPostListResponse, Category, ProductListResponse } from '@/lib/types';

const BASE = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';
const LOCALES = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? 'en,ar').split(',');

function localeAlternates(path: string) {
  const languages: Record<string, string> = {};
  for (const locale of LOCALES) {
    languages[locale] = `${BASE}/${locale}${path}`;
  }
  return { languages };
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const entries: MetadataRoute.Sitemap = [];

  // Static routes — one entry per locale
  const staticPaths = [
    { path: '',              priority: 1.0, changeFrequency: 'weekly' as const },
    { path: '/about',        priority: 0.7, changeFrequency: 'monthly' as const },
    { path: '/contact',      priority: 0.7, changeFrequency: 'monthly' as const },
    { path: '/faq',          priority: 0.6, changeFrequency: 'monthly' as const },
    { path: '/all-products', priority: 0.8, changeFrequency: 'weekly' as const },
    { path: '/blog',         priority: 0.7, changeFrequency: 'weekly' as const },
    { path: '/privacy',      priority: 0.3, changeFrequency: 'yearly' as const },
  ];

  for (const { path, priority, changeFrequency } of staticPaths) {
    for (const locale of LOCALES) {
      entries.push({
        url: `${BASE}/${locale}${path}`,
        priority,
        changeFrequency,
        alternates: localeAlternates(path),
      });
    }
  }

  // No try/catch, and every fetch below throws on a non-2xx or a timeout. A
  // sitemap is served through Next's ISR cache, so a failure that fell through to
  // `return entries` (static-only) or was swallowed to a partial list did not
  // just miss data once — it BAKED a catalogue-less sitemap for the whole
  // `FEED_TTL`, telling crawlers every product URL had vanished. Throwing instead
  // makes Next keep serving the last successfully-generated sitemap until the API
  // is healthy again (F-WEB-6).
  const res = await fetch(`${RSC_API_BASE}/categories`, {
    next: { revalidate: FEED_TTL },
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`sitemap: /categories returned ${res.status}`);

  const categories: Category[] = await res.json();

  for (const c of categories.filter(c => c.is_active)) {
    for (const locale of LOCALES) {
      entries.push({
        url: `${BASE}/${locale}/${c.slug}`,
        lastModified: c.updated_at,
        priority: 0.9,
        changeFrequency: 'daily',
        alternates: localeAlternates(`/${c.slug}`),
      });
    }
  }

  // Product pages — paginate through all active products
  let page = 1;
  let hasMore = true;
  while (hasMore) {
    const prodRes = await fetch(
      `${RSC_API_BASE}/products?per_page=100&page=${page}&is_active=true`,
      { next: { revalidate: FEED_TTL }, signal: AbortSignal.timeout(5000) },
    );
    if (!prodRes.ok) {
      throw new Error(`sitemap: /products page ${page} returned ${prodRes.status}`);
    }

    const data: ProductListResponse = await prodRes.json();
    for (const p of data.items) {
      if (!p.category) continue;
      const productPath = `/${p.category.slug}/${p.slug}`;
      for (const locale of LOCALES) {
        entries.push({
          url: `${BASE}/${locale}${productPath}`,
          lastModified: p.updated_at,
          priority: 0.8,
          changeFrequency: 'weekly',
          alternates: localeAlternates(productPath),
        });
      }
    }

    hasMore = page < data.pages;
    page++;
  }
  // Blog posts
  let blogPage = 1;
  let blogHasMore = true;
  while (blogHasMore) {
    const blogRes = await fetch(
      `${RSC_API_BASE}/blog/public?locale=en&per_page=50&page=${blogPage}`,
      { next: { revalidate: FEED_TTL }, signal: AbortSignal.timeout(5000) },
    );
    if (!blogRes.ok) {
      throw new Error(`sitemap: /blog page ${blogPage} returned ${blogRes.status}`);
    }

    const blogData: BlogPostListResponse = await blogRes.json();
    for (const post of blogData.items) {
      const postPath = `/blog/${post.slug}`;
      for (const locale of LOCALES) {
        entries.push({
          url: `${BASE}/${locale}${postPath}`,
          lastModified: post.updated_at,
          priority: 0.7,
          changeFrequency: 'weekly',
          alternates: localeAlternates(postPath),
        });
      }
    }

    blogHasMore = blogPage < blogData.pages;
    blogPage++;
  }

  return entries;
}
