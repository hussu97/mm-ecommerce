import type { Metadata } from 'next';
import Link from 'next/link';
import type { Category, Product, ProductListResponse } from '@/lib/types';
import { ProductGrid } from '@/components/category/ProductGrid';
import { Breadcrumb } from '@/components/ui';
import { getTranslations, createT } from '@/lib/i18n/server';
import { localizedField } from '@/lib/i18n/entity';
import { RSC_API_BASE } from '@/lib/api-server';
import { CACHE_TAGS, CONTENT_TTL } from '@/lib/cache-policy';
import { getActiveCategories } from '@/lib/catalogue';
import { OG_IMAGE } from '@/lib/schema';
import { SortSelect } from '@/components/category/SortSelect';
import {
  DEFAULT_PRODUCT_SORT,
  parseProductSort,
  productSortLabel,
  productSortOptions,
} from '@/lib/product-sort';
import { fetchJson } from '@/lib/fetch-json';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';
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
    description:
      'The full menu — brownies, cookies, cookie melts, cakes and desserts, baked to order in Sharjah and delivered across the UAE.',
    alternates: {
      canonical: `${SITE_URL}/${locale}/all-products`,
      languages: {
        en: `${SITE_URL}/en/all-products`,
        ar: `${SITE_URL}/ar/all-products`,
        'x-default': `${SITE_URL}/en/all-products`,
      },
    },
    openGraph: {
      title: `${locale === 'ar' ? 'جميع المنتجات' : 'All Products'} | Melting Moments Cakes`,
      description:
        'The full menu — brownies, cookies, cookie melts, cakes and desserts, baked to order and delivered across the UAE.',
      images: [OG_IMAGE],
      locale: locale === 'ar' ? 'ar_AE' : 'en_AE',
    },
  };
}

function CategoryFilterBar({
  categories,
  locale,
  activeCategory,
  sort,
}: {
  categories: Category[];
  locale: string;
  activeCategory?: string;
  sort?: string;
}) {
  // Picking a category must not silently throw away the chosen order.
  const sortParam = sort && sort !== DEFAULT_PRODUCT_SORT ? `sort=${sort}` : '';
  const hrefFor = (slug?: string) => {
    const qs = [slug ? `category=${slug}` : '', sortParam].filter(Boolean).join('&');
    return `/${locale}/all-products${qs ? `?${qs}` : ''}`;
  };
  const allHref = hrefFor();
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
              href={hrefFor(cat.slug)}
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
  searchParams: Promise<{ category?: string; page?: string; sort?: string }>;
}) {
  const { locale } = await params;
  const { category, page: pageStr, sort: sortStr } = await searchParams;
  const page = Math.max(1, parseInt(pageStr ?? '1', 10) || 1);
  const sort = parseProductSort(sortStr);

  const productUrl = `${RSC_API_BASE}/products?per_page=${PER_PAGE}&page=${page}&sort=${sort}${category ? `&category=${category}` : ''}`;

  const [categories, productData, translations] = await Promise.all([
    // Shared with the locale layout's nav bar, so this render asks once.
    getActiveCategories(),
    fetchJson<ProductListResponse>(productUrl, {
      next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
      signal: AbortSignal.timeout(8000),
    }),
    getTranslations(locale),
  ]);

  const t = createT(translations);

  const products: Product[] = productData?.items ?? [];
  const pages = productData?.pages ?? 1;

  // Both the category chip and the sort survive paging.
  const listParams = new URLSearchParams({
    ...(category ? { category } : {}),
    ...(sort === DEFAULT_PRODUCT_SORT ? {} : { sort }),
  }).toString();
  const basePath = `/${locale}/all-products${listParams ? `?${listParams}` : ''}`;
  const baseAbsPath = `${SITE_URL}${basePath}`;
  const sep = basePath.includes('?') ? '&' : '?';
  const prevUrl = page === 2 ? baseAbsPath : `${baseAbsPath}${sep}page=${page - 1}`;
  const nextUrl = `${baseAbsPath}${sep}page=${page + 1}`;

  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: t('breadcrumb.home'), item: `${SITE_URL}/${locale}` },
          { '@type': 'ListItem', position: 2, name: t('nav.all'), item: `${SITE_URL}/${locale}/all-products` },
        ],
      },
      ...(products.length > 0
        ? [
            {
              '@type': 'CollectionPage',
              name: t('nav.all'),
              url: `${SITE_URL}/${locale}/all-products`,
              mainEntity: {
                '@type': 'ItemList',
                numberOfItems: products.length,
                itemListElement: products.map((p, i) => ({
                  '@type': 'ListItem',
                  position: i + 1,
                  name: p.name,
                  url: p.category
                    ? `${SITE_URL}/${locale}/${p.category.slug}/${p.slug}`
                    : `${SITE_URL}/${locale}/${p.slug}`,
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

      <div className="max-w-7xl mx-auto px-4 py-5 sm:py-12">
        <Breadcrumb items={[{ label: t('breadcrumb.home'), href: `/${locale}` }, { label: t('nav.all') }]} />
        <header className="mb-3 sm:mb-8">
          <div className="flex items-center justify-between gap-3 sm:items-end sm:gap-4">
            <h1 className="min-w-0 font-display text-xl sm:text-4xl text-primary uppercase tracking-widest">
              {locale === 'ar' ? 'جميع المنتجات' : 'All Products'}
            </h1>
            {/* Beside the heading — see the matching note on the category page
                for why this needed a change in `SortSelect` rather than here. */}
            <div className="flex justify-end shrink-0">
              <SortSelect
                action={`/${locale}/all-products`}
                surface="category"
                preserved={category ? { category } : undefined}
                value={sort}
                options={productSortOptions(t)}
                label={productSortLabel(t)}
              />
            </div>
          </div>
          <div className="h-px bg-secondary/40 mt-2 sm:mt-4" />
        </header>

        <div className="mb-4 sm:mb-8">
          <CategoryFilterBar categories={categories} locale={locale} activeCategory={category} sort={sort} />
        </div>

        <ProductGrid products={products} list="all_products" />

        <Pagination page={page} pages={pages} basePath={basePath} t={t} />
      </div>
    </>
  );
}
