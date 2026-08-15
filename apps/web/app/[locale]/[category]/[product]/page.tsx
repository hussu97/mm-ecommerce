import type { Metadata } from 'next';
import { notFound, permanentRedirect } from 'next/navigation';
import { Breadcrumb } from '@/components/ui';
import { ProductDetailATC } from './ProductDetailATC';
import { ProductImageGallery } from './ProductImageGallery';
import { RecentlyViewedProducts } from '@/components/product/RecentlyViewedProducts';
import type { Product, ProductListResponse, ProductModifier } from '@/lib/types';
import { localizedField } from '@/lib/i18n/entity';
import { getTranslations, createT } from '@/lib/i18n/server';
import { RSC_API_BASE } from '@/lib/api-server';
import { CACHE_TAGS, CONTENT_TTL, FEED_TTL } from '@/lib/cache-policy';
import {
  BRAND,
  PRODUCT_BRAND,
  buildShippingDetails,
  LOW_ORDER_FEE_SPEC,
  RETURN_POLICY,
  SHIPPING_BY_REGION,
} from '@/lib/schema';
import { fetchJsonOrNull } from '@/lib/fetch-json';
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

/**
 * How long a rendered product page is held before it is built again.
 *
 * Safe for stock: a page up to a minute stale can offer something that has just
 * sold out, but it cannot sell it. `cart_service.add_item` refuses an
 * out-of-stock product, and `order_service` decrements with a conditional
 * UPDATE, so the worst case is a customer told "out of stock" at the basket
 * rather than on the tile.
 *
 * The literal is not a style choice: Next reads segment config statically, and
 * an imported constant fails the build with "Invalid segment configuration
 * export". Keep it in step with `CONTENT_TTL` in `lib/cache-policy.ts`.
 *
 * On its own this does nothing here — see `generateStaticParams` below.
 */
export const revalidate = 60;

/**
 * Prerender the catalogue.
 *
 * `revalidate` on its own is not enough on a segment with dynamic params: Next
 * renders each path on demand and does not hold the result, so every visit to
 * a product page paid for a full render — verified by watching for
 * `x-nextjs-cache` and never seeing it. This export is what actually turns the
 * route into ISR.
 *
 * `dynamicParams` stays at its default of true, so this is a warm start rather
 * than an allow-list: a product added in the admin after the build still
 * renders, it just does not get the benefit of having been rendered already.
 *
 * The catalogue is tens of items, so the build cost is trivial. If the API is
 * unreachable at build time this returns nothing and the whole route falls back
 * to on-demand rendering, which is exactly where it was before.
 */
export async function generateStaticParams() {
  const locales = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? 'en,ar').split(',');
  try {
    const res = await fetch(`${RSC_API_BASE}/products?per_page=500`, {
      next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return [];
    const data = (await res.json()) as ProductListResponse;
    return data.items.flatMap((p) =>
      p.category?.slug
        ? locales.map((locale) => ({
            locale,
            category: p.category!.slug,
            product: p.slug,
          }))
        : [],
    );
  } catch {
    return [];
  }
}

async function getProduct(slug: string): Promise<Product | null> {
  return fetchJsonOrNull<Product>(`${RSC_API_BASE}/products/${slug}`, {
    next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
    signal: AbortSignal.timeout(8000),
  });
}

const FALLBACK_DELIVERY_FEE = 50;

/**
 * Only feeds the shipping markup, so a slow or down rates endpoint must never
 * cost us the page: fall back to the same default the API itself falls back to.
 */
async function getDefaultDeliveryFee(): Promise<number> {
  try {
    const res = await fetch(`${RSC_API_BASE}/delivery/rates`, {
      next: { revalidate: FEED_TTL },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return FALLBACK_DELIVERY_FEE;
    // `default_delivery_fee` used to come from `/delivery/rates` and is gone:
    // a pin outside every zone is now unserviceable rather than charged a
    // national number. This is structured data on a page rendered before any
    // address exists, so it needs *a* figure — the constant is that figure, and
    // the per-region table in `SHIPPING_BY_REGION` is what actually describes
    // the ladder.
    await res.json();
    return FALLBACK_DELIVERY_FEE;
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
    // Every band, plus the fallback rate for an address outside all of them.
    // One `shippingRate` cannot describe a shop that is free in Sharjah and 80
    // in Abu Dhabi; listing the regions lets a shopping surface tell somebody
    // in Ajman something true rather than something averaged.
    shippingDetails: [
      ...SHIPPING_BY_REGION,
      buildShippingDetails(defaultDeliveryFee),
    ],
    // Declared separately because it is not a delivery charge: it does not vary
    // with distance and free delivery does not waive it.
    priceSpecification: LOW_ORDER_FEE_SPEC,
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
