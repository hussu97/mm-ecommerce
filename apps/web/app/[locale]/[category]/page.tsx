import { cache, Suspense } from 'react';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import type { Category, Product, ProductListResponse } from '@/lib/types';
import { ProductGrid } from '@/components/category/ProductGrid';
import { ProductCardSkeleton } from '@/components/ui';
import { CategoryTracker } from '@/components/analytics/CategoryTracker';
import { Breadcrumb } from '@/components/ui';
import { localizedField } from '@/lib/i18n/entity';
import { getTranslations, createT } from '@/lib/i18n/server';
import { RSC_API_BASE } from '@/lib/api-server';
import { CACHE_TAGS, CONTENT_TTL } from '@/lib/cache-policy';
import { SortSelect } from '@/components/category/SortSelect';
import {
  DEFAULT_PRODUCT_SORT,
  parseProductSort,
  productSortLabel,
  productSortOptions,
  type ProductSort,
} from '@/lib/product-sort';
import { fetchJson, fetchJsonOrNull } from '@/lib/fetch-json';
import { branchParam, browsingBranch } from '@/lib/location/branch-server';
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';
/**
 * How many products a listing page asks for.
 *
 * Raised from 12. The whole catalogue is 36 products, so at 12 a shopper had to
 * paginate three times to see a shop that fits on one screen's worth of
 * scrolling — and each page was a fresh server round trip that re-rendered the
 * grid and lost their place.
 *
 * The cost was measured against production rather than guessed: gzipped, the
 * listing goes from 3.9 KB to 13.5 KB, and time-to-first-byte does not move
 * (~0.12s either way) because the extra rows are transfer, not query. The
 * images are the only real weight and they are lazy — `ProductGrid` gives the
 * first four `priority` and `next/image` holds the rest until they are scrolled
 * near, so a longer page costs nothing until it is looked at.
 *
 * 50 rather than exactly 36: the API caps `per_page` at 2000, and a number the
 * catalogue can grow into means the pagination stays real rather than becoming
 * a control that has silently never rendered.
 */
const PER_PAGE = 50;

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
  const category = await fetchJsonOrNull<Category>(`${RSC_API_BASE}/categories/${slug}`, {
    next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
    signal: AbortSignal.timeout(8000),
  });
  return category?.is_active ? category : null;
});

async function getCategoryData(
  slug: string,
  page: number = 1,
  sort: ProductSort = DEFAULT_PRODUCT_SORT,
  branchId: string | null = null,
): Promise<{ category: Category; products: Product[]; total: number; pages: number } | null> {
  // No try/catch that turns a failure into `null`. `null` means notFound(), and
  // under ISR a 404 rendered during a blip is *kept* — a live category gone for
  // the whole TTL. `fetchJsonOrNull` returns null only for a real 404 and
  // throws otherwise, so a broken API produces an error, not a missing page.
  const [category, data] = await Promise.all([
    fetchJsonOrNull<Category>(`${RSC_API_BASE}/categories/${slug}`, {
      next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
      signal: AbortSignal.timeout(8000),
    }),
    fetchJson<ProductListResponse>(
      `${RSC_API_BASE}/products?category=${slug}&per_page=${PER_PAGE}&page=${page}&sort=${sort}${branchParam(branchId)}`,
      {
        next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
        signal: AbortSignal.timeout(8000),
      },
    ),
  ]);

  if (!category || !category.is_active) return null;

  return {
    category,
    products: data?.items ?? [],
    total: data?.total ?? 0,
    pages: data?.pages ?? 1,
  };
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

/**
 * The six-card skeleton that `loading.tsx` used to paint.
 *
 * It lives here now, as the fallback of a boundary *inside* the page, for one
 * reason: a `loading.tsx` makes the whole segment a Suspense boundary, so Next
 * committed a 200 and streamed the shell before this component ever ran — and
 * `notFound()` cannot set a status that has already been sent. Every unknown
 * category URL answered 200 with the 404 body and a `noindex` tag, which Google
 * reads as a live page (`/en/about-me` was indexed exactly this way), and the
 * `permanentRedirect()` on the product route below never redirected anything.
 *
 * Moving the boundary below the existence check keeps the skeleton and gets the
 * status back. The header renders immediately; only the grid waits.
 */
function ProductGridSkeleton() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 sm:gap-8">
      {Array.from({ length: 6 }).map((_, i) => (
        <ProductCardSkeleton key={i} />
      ))}
    </div>
  );
}

/**
 * The half of the page that needs the catalogue.
 *
 * Split out so the products fetch happens under a Suspense boundary rather than
 * in front of the response. Everything here needs the product list — the grid,
 * the page count, the `ItemList` in the JSON-LD, and the analytics event that
 * reports how many results the shopper saw — so it is one component rather than
 * four boundaries.
 *
 * The JSON-LD streams with it. A crawler reads the completed document, not the
 * first flush, so structured data below a boundary is still structured data.
 */
async function CategoryProducts({
  slug,
  locale,
  page,
  sort,
  branchId,
  categoryName,
  basePath,
  t,
}: {
  slug: string;
  locale: string;
  page: number;
  sort: ProductSort;
  branchId: string | null;
  categoryName: string;
  basePath: string;
  t: (key: string, params?: Record<string, string | number>) => string;
}) {
  const data = await getCategoryData(slug, page, sort, branchId);
  // The category existed a moment ago — the page checked before rendering this.
  // A null here means the listing query failed, not that the page is missing.
  if (!data) notFound();

  const { products, pages } = data;

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

      <CategoryTracker categoryName={categoryName} productCount={data.total} />
      <ProductGrid products={products} />
      <Pagination page={page} pages={pages} basePath={basePath} t={t} />
    </>
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

  // The kitchen this shopper's pin resolves to, so the grid is what that
  // kitchen can make rather than what the estate collectively can. Reading the
  // cookie makes this route dynamic for a shopper who has set a location; a
  // crawler and a first visit have none and keep the cached, estate-wide page.
  const branchId = await browsingBranch();

  // Awaited *before* anything renders, which is the whole point of the split.
  // `getCategoryMeta` is one small request and `React.cache`d — `generateMetadata`
  // has already made it — so the cost of asking here is nothing, and the answer
  // is what decides whether this URL is a page at all.
  const [category, translations] = await Promise.all([
    getCategoryMeta(slug),
    getTranslations(locale),
  ]);

  if (!category) notFound();

  const t = createT(translations);
  const categoryName = localizedField(category, 'name', category.name, locale);

  // The sort rides on every listing URL so paging does not silently drop it.
  const basePath = `/${locale}/${slug}${sort === DEFAULT_PRODUCT_SORT ? '' : `?sort=${sort}`}`;

  return (
    <div className="max-w-7xl mx-auto px-4 py-5 sm:py-12">

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

      <Suspense key={`${slug}-${page}-${sort}-${branchId ?? ''}`} fallback={<ProductGridSkeleton />}>
        <CategoryProducts
          slug={slug}
          locale={locale}
          page={page}
          sort={sort}
          branchId={branchId}
          categoryName={categoryName}
          basePath={basePath}
          t={t}
        />
      </Suspense>

    </div>
  );
}
