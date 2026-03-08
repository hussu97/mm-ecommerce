import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import type { Category, Product, ProductListResponse } from '@/lib/types';
import { ProductGrid } from '@/components/category/ProductGrid';
import { Breadcrumb } from '@/components/ui';
import { localizedField } from '@/lib/i18n/entity';
import { getTranslations, createT } from '@/lib/i18n/server';
import { API_BASE } from '@/lib/api';
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';
const PER_PAGE = 12;

async function getCategoryData(
  slug: string,
  page: number = 1,
): Promise<{ category: Category; products: Product[]; total: number; pages: number } | null> {
  try {
    const [catRes, prodRes] = await Promise.all([
      fetch(`${API_BASE}/categories/${slug}`, { next: { revalidate: 300 }, signal: AbortSignal.timeout(8000) }),
      fetch(`${API_BASE}/products?category=${slug}&per_page=${PER_PAGE}&page=${page}`, {
        next: { revalidate: 300 },
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
  const { category: slug } = await params;
  const data = await getCategoryData(slug);
  if (!data) return {};

  const { category } = data;
  const description =
    category.description ??
    `Shop our range of handcrafted ${category.name.toLowerCase()} — made with love in the UAE.`;
  const ogImage = category.image_url
    ? [{ url: category.image_url, alt: category.name }]
    : [{ url: '/images/logos/color_logo.jpeg', alt: 'Melting Moments Cakes' }];

  return {
    title: category.name,
    description,
    openGraph: {
      title: `${category.name} | Melting Moments Cakes`,
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

  return (
    <nav
      className="mt-12 flex items-center justify-center gap-6"
      aria-label="Pagination"
    >
      {page > 1 ? (
        <Link
          href={page === 2 ? basePath : `${basePath}?page=${page - 1}`}
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
          href={`${basePath}?page=${page + 1}`}
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
  searchParams: Promise<{ page?: string }>;
}) {
  const { locale, category: slug } = await params;
  const { page: pageStr } = await searchParams;
  const page = Math.max(1, parseInt(pageStr ?? '1', 10) || 1);

  const [data, translations] = await Promise.all([
    getCategoryData(slug, page),
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
      ...(products.length > 0
        ? [
            {
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

      <div className="max-w-7xl mx-auto px-4 py-12">

        <Breadcrumb items={[{ label: t('breadcrumb.home'), href: `/${locale}` }, { label: categoryName }]} />

        {/* Category header */}
        <header className="mb-10">
          <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest mb-3">
            {categoryName}
          </h1>
          {category.description && (
            <p className="font-body text-sm text-gray-500 max-w-xl">
              {category.description}
            </p>
          )}
          <div className="h-px bg-secondary/40 mt-4" />
        </header>

        {/* Product grid */}
        <ProductGrid products={products} />

        {/* Pagination */}
        <Pagination page={page} pages={pages} basePath={`/${locale}/${slug}`} t={t} />

      </div>
    </>
  );
}
