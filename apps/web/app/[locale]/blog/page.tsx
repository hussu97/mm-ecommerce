import type { Metadata } from 'next';
import Link from 'next/link';
import type { BlogPost, BlogPostListResponse } from '@/lib/types';
import { Breadcrumb } from '@/components/ui';
import { API_BASE } from '@/lib/api';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';
const PER_PAGE = 10;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  return {
    title: 'Blog',
    description: 'Stories, recipes, and inspiration from the Melting Moments kitchen.',
    alternates: {
      canonical: `${SITE_URL}/${locale}/blog`,
      languages: { en: `${SITE_URL}/en/blog`, ar: `${SITE_URL}/ar/blog` },
    },
    openGraph: {
      title: 'Blog | Melting Moments Cakes',
      description: 'Stories, recipes, and inspiration from the Melting Moments kitchen.',
    },
  };
}

function formatDate(iso: string, locale: string): string {
  try {
    return new Date(iso).toLocaleDateString(locale === 'ar' ? 'ar-AE' : 'en-AE', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  } catch {
    return iso.slice(0, 10);
  }
}

export default async function BlogIndexPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ page?: string }>;
}) {
  const { locale } = await params;
  const { page: pageStr } = await searchParams;
  const page = Math.max(1, parseInt(pageStr ?? '1', 10) || 1);

  let posts: BlogPost[] = [];
  let pages = 1;
  let total = 0;

  try {
    const res = await fetch(
      `${API_BASE}/blog/public?locale=${locale}&page=${page}&per_page=${PER_PAGE}`,
      { next: { revalidate: 300 }, signal: AbortSignal.timeout(8000) },
    );
    if (res.ok) {
      const data: BlogPostListResponse = await res.json();
      posts = data.items;
      pages = data.pages;
      total = data.total;
    }
  } catch {
    // render with empty state
  }

  const basePath = `/${locale}/blog`;
  const prevUrl = page === 2 ? `${SITE_URL}${basePath}` : `${SITE_URL}${basePath}?page=${page - 1}`;
  const nextUrl = `${SITE_URL}${basePath}?page=${page + 1}`;

  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: 'Home', item: `${SITE_URL}/${locale}` },
          { '@type': 'ListItem', position: 2, name: 'Blog', item: `${SITE_URL}/${locale}/blog` },
        ],
      },
      ...(posts.length > 0
        ? [
            {
              '@type': 'CollectionPage',
              name: 'Melting Moments Blog',
              url: `${SITE_URL}/${locale}/blog`,
              mainEntity: {
                '@type': 'ItemList',
                numberOfItems: total,
                itemListElement: posts.map((p, i) => ({
                  '@type': 'ListItem',
                  position: (page - 1) * PER_PAGE + i + 1,
                  name: p.content.title ?? p.slug,
                  url: `${SITE_URL}/${locale}/blog/${p.slug}`,
                })),
              },
            },
          ]
        : []),
    ],
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      {page > 1 && <link rel="prev" href={prevUrl} />}
      {page < pages && <link rel="next" href={nextUrl} />}

      <div className="max-w-4xl mx-auto px-4 py-12">
        <Breadcrumb items={[{ label: 'Home', href: `/${locale}` }, { label: 'Blog' }]} />

        <header className="mb-10">
          <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest mb-3">
            Journal
          </h1>
          <p className="font-body text-sm text-gray-500">
            Stories, recipes, and inspiration from the kitchen.
          </p>
          <div className="h-px bg-secondary/40 mt-4" />
        </header>

        {posts.length === 0 ? (
          <p className="font-body text-sm text-gray-400 py-12 text-center">No posts yet. Check back soon.</p>
        ) : (
          <div className="divide-y divide-gray-100">
            {posts.map((post) => {
              const c = post.content;
              return (
                <article key={post.slug} className="py-10 group">
                  <Link href={`/${locale}/blog/${post.slug}`} className="block">
                    <p className="font-body text-[11px] uppercase tracking-widest text-secondary mb-3">
                      {formatDate(post.created_at, locale)}
                    </p>
                    <h2 className="font-display text-2xl sm:text-3xl text-primary mb-3 group-hover:text-secondary transition-colors leading-snug">
                      {c.title ?? post.slug}
                    </h2>
                    {c.excerpt && (
                      <p className="font-body text-sm text-gray-500 leading-relaxed mb-4 max-w-2xl">
                        {c.excerpt}
                      </p>
                    )}
                    {c.tags && c.tags.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-4">
                        {c.tags.map((tag) => (
                          <span
                            key={tag}
                            className="font-body text-[10px] uppercase tracking-widest border border-secondary/50 text-secondary px-2 py-0.5"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    <span className="font-body text-xs uppercase tracking-widest text-primary inline-flex items-center gap-1 hover:underline">
                      Read more →
                    </span>
                  </Link>
                </article>
              );
            })}
          </div>
        )}

        {pages > 1 && (
          <nav className="mt-12 flex items-center justify-center gap-6" aria-label="Pagination">
            {page > 1 ? (
              <Link
                href={page === 2 ? basePath : `${basePath}?page=${page - 1}`}
                className="font-body text-sm text-primary uppercase tracking-widest hover:underline flex items-center gap-1"
              >
                ← Previous
              </Link>
            ) : (
              <span className="font-body text-sm text-gray-300 uppercase tracking-widest">← Previous</span>
            )}
            <span className="font-body text-sm text-gray-500">
              Page {page} of {pages}
            </span>
            {page < pages ? (
              <Link
                href={`${basePath}?page=${page + 1}`}
                className="font-body text-sm text-primary uppercase tracking-widest hover:underline flex items-center gap-1"
              >
                Next →
              </Link>
            ) : (
              <span className="font-body text-sm text-gray-300 uppercase tracking-widest">Next →</span>
            )}
          </nav>
        )}
      </div>
    </>
  );
}
