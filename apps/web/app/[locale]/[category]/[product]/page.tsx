import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { Breadcrumb } from '@/components/ui';
import { ProductDetailATC } from './ProductDetailATC';
import { ProductImageGallery } from './ProductImageGallery';
import { RecentlyViewedProducts } from '@/components/product/RecentlyViewedProducts';
import type { Product } from '@/lib/types';
import { localizedField } from '@/lib/i18n/entity';
import { getTranslations, createT } from '@/lib/i18n/server';
import { API_BASE } from '@/lib/api';
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

async function getProduct(slug: string): Promise<Product | null> {
  const res = await fetch(`${API_BASE}/products/${slug}`, { next: { revalidate: 300 }, signal: AbortSignal.timeout(8000) });
  if (!res.ok) return null;
  return res.json();
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; category: string; product: string }>;
}): Promise<Metadata> {
  const { locale, product: slug } = await params;
  const product = await getProduct(slug);
  if (!product) return {};

  const localizedName = localizedField(product, 'name', product.name, locale);
  const localizedDesc = localizedField(product, 'description', product.description ?? '', locale);
  const description =
    localizedDesc ||
    `Order ${localizedName} — handcrafted with love in the UAE by Melting Moments Cakes.`;
  const ogImages = product.image_urls?.length
    ? product.image_urls.slice(0, 1).map(url => ({ url, alt: localizedName }))
    : [{ url: '/images/logos/color_logo.jpeg', alt: 'Melting Moments Cakes' }];

  return {
    title: localizedName,
    description,
    openGraph: {
      title: `${localizedName} | Melting Moments Cakes`,
      description,
      images: ogImages,
    },
  };
}

export default async function ProductDetailPage({
  params,
}: {
  params: Promise<{ locale: string; category: string; product: string }>;
}) {
  const { locale, category: categorySlug, product: productSlug } = await params;

  const [product, translations] = await Promise.all([
    getProduct(productSlug),
    getTranslations(locale),
  ]);

  if (!product) notFound();
  if (product.category && !product.category.is_active) notFound();

  const t = createT(translations);

  const categoryName = product.category?.name ?? categorySlug;
  const localizedCategoryName = product.category
    ? localizedField(product.category, 'name', categoryName, locale)
    : categoryName;
  const productName = localizedField(product, 'name', product.name, locale);
  const productDescription = localizedField(product, 'description', product.description ?? '', locale);
  const galleryImages = product.image_urls ?? [];

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Product',
    name: productName,
    description: product.description ?? undefined,
    image: galleryImages,
    url: `${SITE_URL}/${locale}/${categorySlug}/${productSlug}`,
    offers: {
      '@type': 'Offer',
      price: Number(product.base_price).toFixed(2),
      priceCurrency: 'AED',
      availability: 'https://schema.org/InStock',
    },
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <div className="max-w-7xl mx-auto px-4 py-12">
        <Breadcrumb
          items={[
            { label: t('breadcrumb.home'), href: `/${locale}` },
            { label: localizedCategoryName, href: `/${locale}/${categorySlug}` },
            { label: productName },
          ]}
        />

        <div className="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-12">
          {/* Images */}
          <ProductImageGallery images={galleryImages} name={product.name} />

          {/* Details + ATC */}
          <div className="flex flex-col gap-6">
            <div>
              <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest mb-3">
                {productName}
              </h1>
              <div className="h-px bg-secondary/40" />
            </div>

            {productDescription && (
              <p className="font-body text-sm text-gray-600 leading-relaxed">
                {productDescription}
              </p>
            )}

            {/* Interactive: variant selector, qty, ATC */}
            <ProductDetailATC product={product} />
          </div>
        </div>
      </div>

      <RecentlyViewedProducts currentSlug={product.slug} locale={locale} />
    </>
  );
}
