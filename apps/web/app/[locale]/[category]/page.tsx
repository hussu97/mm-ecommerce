import { cache } from 'react';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import type { Category, Product, ProductListResponse } from '@/lib/types';
import { ProductGrid } from '@/components/category/ProductGrid';
import { CategoryTracker } from '@/components/analytics/CategoryTracker';
import { Breadcrumb } from '@/components/ui';
import { localizedField } from '@/lib/i18n/entity';
import { getTranslations, createT } from '@/lib/i18n/server';
import { RSC_API_BASE } from '@/lib/api';
import { CACHE_TAGS, CONTENT_TTL } from '@/lib/cache-policy';
import { SortSelect } from '@/components/category/SortSelect';
import {
  DEFAULT_PRODUCT_SORT,
  parseProductSort,
  productSortLabel,
  productSortOptions,
  type ProductSort,
} from '@/lib/product-sort';
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';
const PER_PAGE = 12;

/**
 * Just the category, for `generateMetadata`.
 *
 * Metadata needs a name and a description; it has no use for a page of
 * products. It used to call `getCategoryData` for them anyway and drop
 * everything but `data.category` — and because metadata resolution is its own
 * render pass, with its own `React.cache` scope and its own default arguments,
 * that discarded fetch did not even collapse into the one the page makes. On
 * any page but the first, or any sort but the default, it was a second full
 * catalogue query per render, thrown away on arrival.
 */
const getCategoryMeta = cache(async (slug: string): Promise<Category | null> => {
  try {
    const res = await fetch(`${RSC_API_BASE}/categories/${slug}`, {
      next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    const category: Category = await res.json();
    return category.is_active ? category : null;
  } catch {
    return null;
  }
});

async function getCategoryData(
  slug: string,
  page: number = 1,
  sort: ProductSort = DEFAULT_PRODUCT_SORT,
): Promise<{ category: Category; products: Product[]; total: number; pages: number } | null> {
  try {
    const [catRes, prodRes] = await Promise.all([
      fetch(`${RSC_API_BASE}/categories/${slug}`, {
        next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
        signal: AbortSignal.timeout(8000),
      }),
      fetch(`${RSC_API_BASE}/products?category=${slug}&per_page=${PER_PAGE}&page=${page}&sort=${sort}`, {
        next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
        signal: AbortSignal.timeout(8000),
      }),
    ]);
    if (!catRes.ok) return null;

    const category: Category = await catRes.json();
    if (!category.is_active) return null;

    const data: ProductListResponse | null = prodRes.ok ? await prodRes.json() : null;
    const products = data?.items ?? [];
    const total = data?.total ?? 0;
    const pages = data?.pages ?? 1;

    return { category, products, total, pages };
  } catch (error) {
    console.error('[category] Failed to load data for slug:', slug, error);
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; category: string }>;
}): Promise<Metadata> {
  const { locale, category: slug } = await params;
  const category = await getCategoryMeta(slug);
  if (!category) return {};

  const localizedName = localizedField(category, 'name', category.name, locale);
  const localizedDesc = localizedField(category, 'description', category.description ?? '', locale);
  const description =
    localizedDesc ||
    `Order ${localizedName.toLowerCase()} from Melting Moments Cakes. Baked to order in Sharjah and delivered across Dubai, Sharjah, Ajman and the rest of the UAE.`;
  const ogImage = category.image_url
    ? [{ url: category.image_url, alt: localizedName }]
    : [{ url: '/images/logos/color_logo.jpeg', alt: 'Melting Moments Cakes' }];

  return {
    title: localizedName,
    description,
    alternates: {
      canonical: `${SITE_URL}/${locale}/${slug}`,
      languages: {
        en: `${SITE_URL}/en/${slug}`,
        ar: `${SITE_URL}/ar/${slug}`,
        'x-default': `${SITE_URL}/en/${slug}`,
      },
    },
    openGraph: {
      title: `${localizedName} | Melting Moments Cakes`,
      description,
      images: ogImage,
    },
  };
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

  return (
    <nav
      className="mt-12 flex items-center justify-center gap-6"
      aria-label="Pagination"
    >
      {page > 1 ? (
        <Link
          href={page === 2 ? basePath : `${basePath}${sep}page=${page - 1}`}
          className="font-body text-sm text-primary uppercase tracking-widest hover:underline flex items-center gap-1"
        >
          ← {t('common.previous')}
        </Link>
      ) : (
        <span className="font-body text-sm text-gray-300 uppercase tracking-widest">← {t('common.previous')}</span>
      )}

      <span className="font-body text-sm text-gray-500">
        {t('common.page_of', { page, pages })}
      </span>

      {page < pages ? (
        <Link
          href={`${basePath}${sep}page=${page + 1}`}
          className="font-body text-sm text-primary uppercase tracking-widest hover:underline flex items-center gap-1"
        >
          {t('common.next')} →
        </Link>
      ) : (
        <span className="font-body text-sm text-gray-300 uppercase tracking-widest">{t('common.next')} →</span>
      )}
    </nav>
  );
}

export default async function CategoryPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string; category: string }>;
  searchParams: Promise<{ page?: string; sort?: string }>;
}) {
  const { locale, category: slug } = await params;
  const { page: pageStr, sort: sortStr } = await searchParams;
  const page = Math.max(1, parseInt(pageStr ?? '1', 10) || 1);
  const sort = parseProductSort(sortStr);

  const [data, translations] = await Promise.all([
    getCategoryData(slug, page, sort),
    getTranslations(locale),
  ]);

  if (!data) notFound();

  const t = createT(translations);
  const { category, products, pages } = data;
  const categoryName = localizedField(category, 'name', category.name, locale);

  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: t('breadcrumb.home'), item: `${SITE_URL}/${locale}` },
          { '@type': 'ListItem', position: 2, name: categoryName, item: `${SITE_URL}/${locale}/${slug}` },
        ],
      },
      {
        '@type': 'CollectionPage',
        name: categoryName,
        url: `${SITE_URL}/${locale}/${slug}`,
        ...(products.length > 0
          ? {
              mainEntity: {
                '@type': 'ItemList',
                name: categoryName,
                numberOfItems: products.length,
                itemListElement: products.map((p, i) => ({
                  '@type': 'ListItem',
                  position: i + 1,
                  name: p.name,
                  url: `${SITE_URL}/${locale}/${slug}/${p.slug}`,
                })),
              },
            }
          : {}),
      },
    ],
  };

  // The sort rides on every listing URL so paging does not silently drop it.
  const basePath = `/${locale}/${slug}${sort === DEFAULT_PRODUCT_SORT ? '' : `?sort=${sort}`}`;
  const baseAbsPath = `${SITE_URL}${basePath}`;
  const sep = basePath.includes('?') ? '&' : '?';
  const prevUrl = page === 2 ? baseAbsPath : `${baseAbsPath}${sep}page=${page - 1}`;
  const nextUrl = `${baseAbsPath}${sep}page=${page + 1}`;

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      {page > 1 && <link rel="prev" href={prevUrl} />}
      {page < pages && <link rel="next" href={nextUrl} />}

      <div className="max-w-7xl mx-auto px-4 py-5 sm:py-12">

        <CategoryTracker categoryName={categoryName} productCount={data.total} />
        <Breadcrumb items={[{ label: t('breadcrumb.home'), href: `/${locale}` }, { label: categoryName }]} />

        {/* Category header — trimmed on phones so the grid starts inside the
            first screen rather than a scroll below it. */}
        <header className="mb-4 sm:mb-10">
          <div className="flex items-center justify-between gap-3 sm:items-end sm:gap-4">
            <div className="min-w-0">
              <h1 className="font-display text-xl sm:text-4xl text-primary uppercase tracking-widest mb-1 sm:mb-3">
                {categoryName}
              </h1>
              {category.description && (
                <p className="font-body text-xs sm:text-sm text-gray-500 max-w-xl line-clamp-1 sm:line-clamp-none">
                  {category.description}
                </p>
              )}
            </div>
            {/* Beside the heading, not under it. It used to take its own line on
                phones because the control is forced to 16px there — the
                anti-zoom rule in globals.css, which is unlayered and so beats
                any utility class — and at 16px uppercase with wide tracking the
                longest option was too wide to share a row. `SortSelect` drops
                the tracking and the uppercase below `sm`, which is what makes
                it fit; the row itself was never the problem. */}
            <div className="flex justify-end shrink-0">
              <SortSelect
                action={`/${locale}/${slug}`}
                surface="category"
                value={sort}
                options={productSortOptions(t)}
                label={productSortLabel(t)}
              />
            </div>
          </div>
          <div className="h-px bg-secondary/40 mt-2 sm:mt-4" />
        </header>

        {/* Product grid */}
        <ProductGrid products={products} />

        {/* Pagination */}
        <Pagination page={page} pages={pages} basePath={basePath} t={t} />

      </div>
    </>
  );
}
