import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import type { Category, Product, ProductListResponse } from '@/lib/types';
import { ProductGrid } from '@/components/category/ProductGrid';
import { Breadcrumb } from '@/components/ui';

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';

async function getCategoryData(
  slug: string,
): Promise<{ category: Category; products: Product[] } | null> {
  try {
    const [catRes, prodRes] = await Promise.all([
      fetch(`${API_BASE}/categories/${slug}`, { next: { revalidate: 300 } }),
      fetch(`${API_BASE}/products?category=${slug}&per_page=50`, { next: { revalidate: 300 } }),
    ]);
    if (!catRes.ok) return null;

    const category: Category = await catRes.json();
    if (!category.is_active) return null;
    const products: Product[] = prodRes.ok
      ? ((await prodRes.json()) as ProductListResponse).items ?? []
      : [];

    return { category, products };
  } catch (error) {
    console.error('[category] Failed to load data for slug:', slug, error);
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ category: string }>;
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

export default async function CategoryPage({
  params,
}: {
  params: Promise<{ category: string }>;
}) {
  const { category: slug } = await params;
  const data = await getCategoryData(slug);

  if (!data) notFound();

  const { category, products } = data;

  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: 'Home', item: 'https://meltingmomentscakes.com' },
          { '@type': 'ListItem', position: 2, name: category.name, item: `https://meltingmomentscakes.com/${slug}` },
        ],
      },
      ...(products.length > 0
        ? [
            {
              '@type': 'ItemList',
              name: category.name,
              numberOfItems: products.length,
              itemListElement: products.map((p, i) => ({
                '@type': 'ListItem',
                position: i + 1,
                name: p.name,
                url: `https://meltingmomentscakes.com/${slug}/${p.slug}`,
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

        <Breadcrumb items={[{ label: 'Home', href: '/' }, { label: category.name }]} />

        {/* Category header */}
        <header className="mb-10">
          <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest mb-3">
            {category.name}
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

      </div>
    </>
  );
}
