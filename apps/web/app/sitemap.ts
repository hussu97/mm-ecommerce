import type { MetadataRoute } from 'next';

const BASE = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const staticRoutes: MetadataRoute.Sitemap = [
    { url: BASE,                  priority: 1.0, changeFrequency: 'weekly' },
    { url: `${BASE}/about`,       priority: 0.7, changeFrequency: 'monthly' },
    { url: `${BASE}/contact`,     priority: 0.7, changeFrequency: 'monthly' },
    { url: `${BASE}/faq`,         priority: 0.6, changeFrequency: 'monthly' },
  ];

  try {
    const res = await fetch(`${API_BASE}/categories`, { next: { revalidate: 3600 } });
    if (!res.ok) return staticRoutes;

    const categories: Array<{ slug: string; is_active: boolean; updated_at: string }> = await res.json();

    const categoryRoutes: MetadataRoute.Sitemap = categories
      .filter(c => c.is_active)
      .map(c => ({
        url: `${BASE}/${c.slug}`,
        lastModified: c.updated_at,
        priority: 0.9,
        changeFrequency: 'daily' as const,
      }));

    return [...staticRoutes, ...categoryRoutes];
  } catch {
    return staticRoutes;
  }
}
