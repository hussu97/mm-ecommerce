import type { Metadata } from 'next';
import Link from 'next/link';
import type { Category, Product, ProductListResponse } from '@/lib/types';
import { ProductGrid } from '@/components/category/ProductGrid';
import { getTranslations, createT } from '@/lib/i18n/server';
import { localizedField } from '@/lib/i18n/entity';
import { API_BASE } from '@/lib/api';

const PER_PAGE = 12;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const translations = await getTranslations(locale);
  const t = createT(translations);
  return {
    title: t('nav.all'),
    description: 'Browse our full range of handcrafted treats — made with love in the UAE.',
  };
}

function CategoryFilterBar({
  categories,
  locale,
  activeCategory,
}: {
  categories: Category[];
  locale: string;
  activeCategory?: string;
}) {
  const allHref = `/${locale}/all-products`;
  const allActive = !activeCategory;

  return (
    <div className="overflow-x-auto scrollbar-none -mx-4 px-4 sm:mx-0 sm:px-0">
      <div className="flex items-center gap-2 sm:flex-wrap py-1">
        <Link
          href={allHref}
          className={[
            'font-body text-[11px] uppercase tracking-widest border px-3 py-1 whitespace-nowrap transition-colors',
            allActive
              ? 'text-primary border-primary bg-primary/5'
              : 'text-gray-500 border-gray-200 hover:text-primary hover:border-primary/40',
          ].join(' ')}
        >
          All
        </Link>
        {categories.map((cat) => {
          const name = localizedField(cat, 'name', cat.name, locale);
          const isActive = activeCategory === cat.slug;
          return (
            <Link
              key={cat.slug}
              href={`/${locale}/all-products?category=${cat.slug}`}
              className={[
                'font-body text-[11px] uppercase tracking-widest border px-3 py-1 whitespace-nowrap transition-colors',
                isActive
                  ? 'text-primary border-primary bg-primary/5'
                  : 'text-gray-500 border-gray-200 hover:text-primary hover:border-primary/40',
              ].join(' ')}
            >
              {name}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function Pagination({
  page,
  pages,
  basePath,
  t,
}: {
  page: number;
  pages: number;
  basePath: string;
  t: (key: string, params?: Record<string, string | number>) => string;
}) {
  if (pages <= 1) return null;

  const sep = basePath.includes('?') ? '&' : '?';
  const prevHref = page === 2 ? basePath : `${basePath}${sep}page=${page - 1}`;
  const nextHref = `${basePath}${sep}page=${page + 1}`;

  return (
    <nav className="mt-12 flex items-center justify-center gap-6" aria-label="Pagination">
      {page > 1 ? (
        <Link
          href={prevHref}
          className="font-body text-sm text-primary uppercase tracking-widest hover:underline flex items-center gap-1"
        >
          ← {t('common.previous')}
        </Link>
      ) : (
        <span className="font-body text-sm text-gray-300 uppercase tracking-widest">
          ← {t('common.previous')}
        </span>
      )}

      <span className="font-body text-sm text-gray-500">
        {t('common.page_of', { page, pages })}
      </span>

      {page < pages ? (
        <Link
          href={nextHref}
          className="font-body text-sm text-primary uppercase tracking-widest hover:underline flex items-center gap-1"
        >
          {t('common.next')} →
        </Link>
      ) : (
        <span className="font-body text-sm text-gray-300 uppercase tracking-widest">
          {t('common.next')} →
        </span>
      )}
    </nav>
  );
}

export default async function AllProductsPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ category?: string; page?: string }>;
}) {
  const { locale } = await params;
  const { category, page: pageStr } = await searchParams;
  const page = Math.max(1, parseInt(pageStr ?? '1', 10) || 1);

  const productUrl = `${API_BASE}/products?per_page=${PER_PAGE}&page=${page}${category ? `&category=${category}` : ''}`;

  const [categoriesRes, productsRes, translations] = await Promise.all([
    fetch(`${API_BASE}/categories`, { next: { revalidate: 300 }, signal: AbortSignal.timeout(8000) }),
    fetch(productUrl, { next: { revalidate: 300 }, signal: AbortSignal.timeout(8000) }),
    getTranslations(locale),
  ]);

  const t = createT(translations);

  const allCategories: Category[] = categoriesRes.ok ? await categoriesRes.json() : [];
  const categories = allCategories.filter((c) => c.is_active).sort((a, b) => a.display_order - b.display_order);

  const productData: ProductListResponse | null = productsRes.ok ? await productsRes.json() : null;
  const products: Product[] = productData?.items ?? [];
  const pages = productData?.pages ?? 1;

  const basePath = `/${locale}/all-products${category ? `?category=${category}` : ''}`;

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <header className="mb-8">
        <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest mb-3">
          {t('nav.all')}
        </h1>
        <div className="h-px bg-secondary/40 mt-4" />
      </header>

      <div className="mb-8">
        <CategoryFilterBar categories={categories} locale={locale} activeCategory={category} />
      </div>

      <ProductGrid products={products} />

      <Pagination page={page} pages={pages} basePath={basePath} t={t} />
    </div>
  );
}
