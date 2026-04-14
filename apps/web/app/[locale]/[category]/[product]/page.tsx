import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { Breadcrumb } from '@/components/ui';
import { ProductDetailATC } from './ProductDetailATC';
import { ProductImageGallery } from './ProductImageGallery';
import { RecentlyViewedProducts } from '@/components/product/RecentlyViewedProducts';
import type { Product, ProductModifier } from '@/lib/types';
import { localizedField } from '@/lib/i18n/entity';
import { getTranslations, createT } from '@/lib/i18n/server';
import { API_BASE } from '@/lib/api';
import { BRAND, PRODUCT_BRAND, SHIPPING_DETAILS, RETURN_POLICY } from '@/lib/schema';
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
  const { locale, category: categorySlug, product: slug } = await params;
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
  const path = `/${categorySlug}/${slug}`;

  return {
    title: localizedName,
    description,
    alternates: {
      canonical: `${SITE_URL}/${locale}${path}`,
      languages: { en: `${SITE_URL}/en${path}`, ar: `${SITE_URL}/ar${path}` },
    },
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

  // Compute price range from modifier options
  const basePrice = Number(product.base_price);
  const hasModifierPrices = product.product_modifiers?.some(
    (pm: ProductModifier) => pm.modifier.options.some(o => o.price > 0),
  );

  let offers: Record<string, unknown>;
  const offerUrl = `${SITE_URL}/${locale}/${categorySlug}/${productSlug}`;
  const availability = product.is_active
    ? 'https://schema.org/InStock'
    : 'https://schema.org/OutOfStock';

  if (hasModifierPrices) {
    // Sum the max option price from each modifier group to get the highest possible price
    const maxExtra = product.product_modifiers.reduce(
      (sum: number, pm: ProductModifier) => {
        const maxOptionPrice = Math.max(0, ...pm.modifier.options.map(o => o.price));
        return sum + maxOptionPrice;
      },
      0,
    );
    offers = {
      '@type': 'AggregateOffer',
      lowPrice: basePrice.toFixed(2),
      highPrice: (basePrice + maxExtra).toFixed(2),
      priceCurrency: 'AED',
      availability,
      offerCount: 2,
      url: offerUrl,
      seller: BRAND,
      shippingDetails: SHIPPING_DETAILS,
      hasMerchantReturnPolicy: RETURN_POLICY,
    };
  } else {
    offers = {
      '@type': 'Offer',
      price: basePrice.toFixed(2),
      priceCurrency: 'AED',
      availability,
      seller: BRAND,
      url: offerUrl,
      itemCondition: 'https://schema.org/NewCondition',
      priceValidUntil: '2027-03-09',
      shippingDetails: SHIPPING_DETAILS,
      hasMerchantReturnPolicy: RETURN_POLICY,
    };
  }

  const productSchema: Record<string, unknown> = {
    '@type': 'Product',
    '@id': `${SITE_URL}/products/${productSlug}`,
    name: productName,
    description: product.description ?? undefined,
    image: galleryImages,
    url: offerUrl,
    brand: PRODUCT_BRAND,
    category: localizedCategoryName,
    offers,
  };
  if (product.sku) {
    productSchema.sku = product.sku;
    productSchema.mpn = product.sku;
  } else {
    productSchema.mpn = productSlug;
  }
  if (product.calories) {
    productSchema.nutrition = {
      '@type': 'NutritionInformation',
      calories: `${product.calories} cal`,
    };
  }

  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      productSchema,
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: t('breadcrumb.home'), item: `${SITE_URL}/${locale}` },
          { '@type': 'ListItem', position: 2, name: localizedCategoryName, item: `${SITE_URL}/${locale}/${categorySlug}` },
          { '@type': 'ListItem', position: 3, name: productName },
        ],
      },
    ],
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
