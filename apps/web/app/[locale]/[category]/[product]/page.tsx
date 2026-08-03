import type { Metadata } from 'next';
import { notFound, permanentRedirect } from 'next/navigation';
import { Breadcrumb } from '@/components/ui';
import { ProductDetailATC } from './ProductDetailATC';
import { ProductImageGallery } from './ProductImageGallery';
import { RecentlyViewedProducts } from '@/components/product/RecentlyViewedProducts';
import type { Product, ProductModifier } from '@/lib/types';
import { localizedField } from '@/lib/i18n/entity';
import { getTranslations, createT } from '@/lib/i18n/server';
import { RSC_API_BASE } from '@/lib/api';
import { BRAND, PRODUCT_BRAND, buildShippingDetails, RETURN_POLICY } from '@/lib/schema';
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

async function getProduct(slug: string): Promise<Product | null> {
  const res = await fetch(`${RSC_API_BASE}/products/${slug}`, { next: { revalidate: 300 }, signal: AbortSignal.timeout(8000) });
  if (!res.ok) return null;
  return res.json();
}

const FALLBACK_DELIVERY_FEE = 50;

/**
 * Only feeds the shipping markup, so a slow or down rates endpoint must never
 * cost us the page: fall back to the same default the API itself falls back to.
 */
async function getDefaultDeliveryFee(): Promise<number> {
  try {
    const res = await fetch(`${RSC_API_BASE}/delivery/rates`, {
      next: { revalidate: 3600 },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return FALLBACK_DELIVERY_FEE;
    const rates = await res.json();
    const fee = Number(rates?.default_delivery_fee);
    return Number.isFinite(fee) ? fee : FALLBACK_DELIVERY_FEE;
  } catch {
    return FALLBACK_DELIVERY_FEE;
  }
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
    `Order ${localizedName} from Melting Moments Cakes. Baked to order in Sharjah, delivered across Dubai, Sharjah, Ajman and the rest of the UAE.`;
  const ogImages = product.image_urls?.length
    ? product.image_urls.slice(0, 1).map(url => ({ url, alt: localizedName }))
    : [{ url: '/images/logos/color_logo.jpeg', alt: 'Melting Moments Cakes' }];
  // Canonicalise on the product's real category, never on whatever slug the
  // request happened to use — otherwise /en/product-page/x and /en/cat-cookiemelt/x
  // each declare themselves canonical and split the ranking signal.
  const path = `/${product.category?.slug ?? categorySlug}/${slug}`;

  return {
    title: localizedName,
    description,
    alternates: {
      canonical: `${SITE_URL}/${locale}${path}`,
      languages: {
        en: `${SITE_URL}/en${path}`,
        ar: `${SITE_URL}/ar${path}`,
        'x-default': `${SITE_URL}/en${path}`,
      },
    },
    openGraph: {
      title: `${localizedName} | Melting Moments Cakes`,
      description,
      images: ogImages,
      locale: locale === 'ar' ? 'ar_AE' : 'en_AE',
    },
  };
}

export default async function ProductDetailPage({
  params,
}: {
  params: Promise<{ locale: string; category: string; product: string }>;
}) {
  const { locale, category: categorySlug, product: productSlug } = await params;

  const [product, translations, defaultDeliveryFee] = await Promise.all([
    getProduct(productSlug),
    getTranslations(locale),
    getDefaultDeliveryFee(),
  ]);

  if (!product) notFound();
  if (product.category && !product.category.is_active) notFound();

  // The route matches any [category] segment, so the same product was reachable
  // — and indexable — under an unlimited number of URLs. Send every variant to
  // the one real address instead of serving duplicates.
  if (product.category?.slug && product.category.slug !== categorySlug) {
    permanentRedirect(`/${locale}/${product.category.slug}/${productSlug}`);
  }

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

  const offerUrl = `${SITE_URL}/${locale}/${categorySlug}/${productSlug}`;
  const availability = product.is_active
    ? 'https://schema.org/InStock'
    : 'https://schema.org/OutOfStock';

  // Google Merchant Center requires `price` on Offer — AggregateOffer has no
  // `price` attribute — so a modifier-priced item publishes the lowest price
  // actually reachable: base plus the cheapest option of every required group.
  const minExtra = hasModifierPrices
    ? product.product_modifiers.reduce((sum: number, pm: ProductModifier) => {
        if (pm.minimum_options === 0) return sum;
        const minOptionPrice = Math.min(...pm.modifier.options.map(o => o.price));
        return sum + Math.max(0, minOptionPrice);
      }, 0)
    : 0;

  const offers: Record<string, unknown> = {
    '@type': 'Offer',
    price: (basePrice + minExtra).toFixed(2),
    priceCurrency: 'AED',
    availability,
    url: offerUrl,
    seller: BRAND,
    itemCondition: 'https://schema.org/NewCondition',
    // Search Console asks for `validFrom`; the offer has stood since the
    // product was created, and that date does not churn on every edit the way
    // updated_at would — which would re-date the markup for a typo fix.
    validFrom: product.created_at.slice(0, 10),
    priceValidUntil: '2100-01-01',
    shippingDetails: buildShippingDetails(defaultDeliveryFee),
    hasMerchantReturnPolicy: RETURN_POLICY,
  };

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
      {
        '@type': 'WebPage',
        '@id': `${offerUrl}#webpage`,
        speakable: {
          '@type': 'SpeakableSpecification',
          cssSelector: ['h1', '#product-description'],
        },
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
              <p id="product-description" className="font-body text-sm text-gray-600 leading-relaxed">
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
