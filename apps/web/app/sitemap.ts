import type { MetadataRoute } from 'next';

import { API_BASE } from '@/lib/api';

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
    { path: '',        priority: 1.0, changeFrequency: 'weekly' as const },
    { path: '/about',  priority: 0.7, changeFrequency: 'monthly' as const },
    { path: '/contact', priority: 0.7, changeFrequency: 'monthly' as const },
    { path: '/faq',    priority: 0.6, changeFrequency: 'monthly' as const },
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
    const res = await fetch(`${API_BASE}/categories`, { next: { revalidate: 3600 } });
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
  } catch {
    // fallback to static only
  }

  return entries;
}
