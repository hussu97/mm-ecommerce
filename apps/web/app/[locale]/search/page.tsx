import type { Metadata } from 'next';
import Link from 'next/link';
import type { ProductListResponse } from '@/lib/types';
import { API_BASE } from '@/lib/api';
import { ProductGrid } from '@/components/category/ProductGrid';
import { getTranslations, createT } from '@/lib/i18n/server';

interface SearchParams {
  q?: string;
  category?: string;
  sort?: string;
  page?: string;
}

async function searchProducts(params: SearchParams): Promise<ProductListResponse | null> {
  const { q, category, sort, page } = params;
  if (!q) return null;

  const qs = new URLSearchParams({
    search: q,
    per_page: '20',
    ...(category && { category }),
    ...(sort && { sort }),
    ...(page && { page }),
  });

  const res = await fetch(`${API_BASE}/products?${qs.toString()}`, { cache: 'no-store' });
  if (!res.ok) return null;
  return res.json();
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const translations = await getTranslations(locale);
  const t = createT(translations);
  return {
    title: t('search.title'),
    description: 'Search our handcrafted brownies, cookies and desserts.',
    robots: { index: false, follow: true },
  };
}

export default async function SearchPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { locale } = await params;
  const sp = await searchParams;
  const { q, page } = sp;
  const currentPage = Number(page ?? 1);

  const translations = await getTranslations(locale);
  const t = createT(translations);

  const data = await searchProducts(sp);

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <header className="mb-10">
        <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest mb-3">
          {q ? t('search.results_for', { q }) : t('search.title')}
        </h1>
        {q && data && (
          <p className="font-body text-sm text-gray-400">
            {data.total} {data.total === 1 ? t('search.product_singular') : t('search.product_plural')} found
          </p>
        )}
        <div className="h-px bg-secondary/40 mt-4" />
      </header>

      {!q && (
        <div className="text-center py-24">
          <span className="material-icons text-5xl text-secondary mb-4 block">search</span>
          <p className="font-body text-gray-400 text-sm uppercase tracking-widest">
            {t('search.empty_prompt')}
          </p>
        </div>
      )}

      {q && data && data.items.length === 0 && (
        <div className="text-center py-24">
          <span className="material-icons text-5xl text-secondary mb-4 block">inventory_2</span>
          <p className="font-body text-gray-500 text-sm uppercase tracking-widest mb-2">
            {t('search.no_results', { q })}
          </p>
          <p className="font-body text-gray-400 text-xs">
            Try a different search term or browse our{' '}
            <Link href="/" className="text-primary underline">
              {t('search.categories_link')}
            </Link>
            .
          </p>
        </div>
      )}

      {q && data && data.items.length > 0 && (
        <>
          <ProductGrid products={data.items} />

          {/* Pagination */}
          {data.pages > 1 && (
            <div className="flex items-center justify-center gap-4 mt-12">
              {currentPage > 1 && (
                <Link
                  href={`/search?q=${encodeURIComponent(q)}&page=${currentPage - 1}`}
                  className="text-xs font-body uppercase tracking-widest text-primary hover:opacity-70 transition-opacity border border-primary px-4 py-2"
                >
                  {t('common.previous')}
                </Link>
              )}
              <span className="text-xs font-body text-gray-400">
                {t('common.page_of', { page: String(currentPage), pages: String(data.pages) })}
              </span>
              {currentPage < data.pages && (
                <Link
                  href={`/search?q=${encodeURIComponent(q)}&page=${currentPage + 1}`}
                  className="text-xs font-body uppercase tracking-widest text-primary hover:opacity-70 transition-opacity border border-primary px-4 py-2"
                >
                  {t('common.next')}
                </Link>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
