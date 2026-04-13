import type { MetadataRoute } from 'next';

import { API_BASE } from '@/lib/api';
import type { BlogPostListResponse, ProductListResponse } from '@/lib/types';

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

  try {
    const res = await fetch(`${API_BASE}/categories`, {
      next: { revalidate: 3600 },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return entries;

    const categories: Array<{ slug: string; is_active: boolean; updated_at: string }> = await res.json();

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
        `${API_BASE}/products?per_page=100&page=${page}&is_active=true`,
        { next: { revalidate: 3600 }, signal: AbortSignal.timeout(5000) },
      );
      if (!prodRes.ok) break;

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
        `${API_BASE}/blog/public?locale=en&per_page=50&page=${blogPage}`,
        { next: { revalidate: 3600 }, signal: AbortSignal.timeout(5000) },
      );
      if (!blogRes.ok) break;

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
  } catch {
    // fallback to static only
  }

  return entries;
}
