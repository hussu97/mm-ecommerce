import { cache } from 'react';
import type { Metadata } from 'next';
import { notFound, permanentRedirect } from 'next/navigation';
import { Breadcrumb } from '@/components/ui';
import { ProductDetailATC } from './ProductDetailATC';
import { ProductImageGallery } from './ProductImageGallery';
import { ProductBadge } from '@/components/product/ProductBadge';
import { RecentlyViewedProducts } from '@/components/product/RecentlyViewedProducts';
import type { Product, ProductListResponse } from '@/lib/types';
import { resolveProductBadge, badgeI18nKey, BADGE_FALLBACK } from '@/lib/product-badge';
import { withFallback } from '@/lib/i18n/fallback';
import { localizedField } from '@/lib/i18n/entity';
import { getTranslations, createT } from '@/lib/i18n/server';
import { RSC_API_BASE } from '@/lib/api-server';
import { CACHE_TAGS, CONTENT_TTL, FEED_TTL } from '@/lib/cache-policy';
import { offerPrice } from '@/lib/pricing';
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

/**
 * `React.cache`d so `generateMetadata` and the page component — two separate
 * calls with the same `slug` — collapse into one fetch per request instead of
 * paying for the product twice on every render.
 */
const getProduct = cache(async (slug: string): Promise<Product | null> => {
  return fetchJsonOrNull<Product>(`${RSC_API_BASE}/products/${slug}`, {
    next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
    signal: AbortSignal.timeout(8000),
  });
});

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
  // `getProduct` throws on a 5xx/timeout by design (see `fetchJsonOrNull`), so
  // that a broken API never gets ISR-cached as "not found". Metadata resolution
  // runs before the page itself gets a chance to render or hit an error
  // boundary, so a throw here is a bare SSR crash instead of a 500 page — worth
  // losing the tags for a render, never worth losing the page.
  let product: Product | null;
  try {
    product = await getProduct(slug);
  } catch {
    return {};
  }
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

/**
 * The `Offer` for a product's JSON-LD, or `undefined` when there is nothing
 * honest to price.
 *
 * Pulled out of the page component and exported so the bug it replaces has
 * something a test can call directly. That bug: `hasModifierPrices` checked
 * every option for `price > 0` with no `is_active` filter, and `minExtra`
 * took `Math.min()` of a group's options with no guard for an empty array —
 * `Math.min()` of nothing is `Infinity`, which a modifier group left with no
 * active options (or none at all) published straight into `Offer.price`.
 *
 * Goes through `offerPrice` — built on the single `computeFromPrice` in
 * `lib/pricing.ts`, the same function the product card and the homepage's
 * Menu schema use — so this is one of three callers of one calculation
 * rather than a fourth reimplementation of it.
 */
export function buildProductOffer(
  product: Product,
  opts: { offerUrl: string; defaultDeliveryFee: number },
): Record<string, unknown> | undefined {
  const price = offerPrice(product);
  if (price === null) return undefined;

  const availability = product.is_active
    ? 'https://schema.org/InStock'
    : 'https://schema.org/OutOfStock';

  return {
    '@type': 'Offer',
    price: price.toFixed(2),
    priceCurrency: 'AED',
    availability,
    url: opts.offerUrl,
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
      buildShippingDetails(opts.defaultDeliveryFee),
    ],
    // Declared separately because it is not a delivery charge: it does not
    // vary with distance and free delivery does not waive it.
    priceSpecification: LOW_ORDER_FEE_SPEC,
    hasMerchantReturnPolicy: RETURN_POLICY,
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

  // ── Do not add a `loading.tsx` to this segment ─────────────────────────────
  //
  // There used to be one, and it is why none of the three lines below did
  // anything. A `loading.tsx` makes the whole segment a Suspense boundary, so
  // Next committed a 200 and streamed the shell before this function ran:
  // `notFound()` and `permanentRedirect()` were then setting a status that had
  // already gone to the client. Measured on production before it was removed —
  // `/en/cat-brownies/mix-cookies-box-of-9`, a real product under the wrong
  // category, answered 200 and rendered the product, with the canonical tag
  // doing all the work the redirect was supposed to do.
  //
  // The cost of not having one is that a click on a product card waits for
  // `getProduct` before anything paints, instead of showing a skeleton. That is
  // one API call behind a 60-second cache, and there is nothing on this page
  // that does not need its answer, so there was never anything to stream in the
  // meantime — the skeleton was buying a paint, not a fetch. The category route
  // keeps its skeleton because its grid genuinely can stream; see the
  // `<Suspense>` in `[category]/page.tsx`.
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

  const offerUrl = `${SITE_URL}/${locale}/${categorySlug}/${productSlug}`;
  const offers = buildProductOffer(product, { offerUrl, defaultDeliveryFee });

  const productSchema: Record<string, unknown> = {
    '@type': 'Product',
    '@id': `${SITE_URL}/products/${productSlug}`,
    name: productName,
    description: product.description ?? undefined,
    image: galleryImages,
    url: offerUrl,
    brand: PRODUCT_BRAND,
    category: localizedCategoryName,
    ...(offers ? { offers } : {}),
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

  // The corner flag follows the product onto its own page — the launch item
  // reads as "Website Exclusive" here too, with a line saying what that means.
  const badgeVariant = resolveProductBadge(product);
  const badgeText = badgeVariant
    ? withFallback(t, badgeI18nKey(badgeVariant), BADGE_FALLBACK[badgeVariant])
    : undefined;

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
          {/* Images — the badge floats over the gallery, as it does on the tile */}
          <div className="relative">
            <ProductImageGallery images={galleryImages} name={product.name} />
            {badgeVariant && badgeText && (
              <ProductBadge variant={badgeVariant}>{badgeText}</ProductBadge>
            )}
          </div>

          {/* Details + ATC */}
          <div className="flex flex-col gap-6">
            <div>
              <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest mb-3">
                {productName}
              </h1>
              <div className="h-px bg-secondary/40" />
            </div>

            {badgeVariant === 'website_exclusive' && (
              <div className="flex items-center gap-2.5 border-s-2 border-amber-400 bg-gradient-to-r from-amber-200/75 to-amber-100/30 px-4 py-2.5 text-amber-950">
                <svg
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                  className="h-3.5 w-3.5 shrink-0 fill-amber-600"
                >
                  <path d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8z" />
                </svg>
                <span className="font-body text-[11px] uppercase tracking-[0.22em]">
                  {withFallback(t, 'product.website_exclusive_note', 'Only on our website')}
                </span>
              </div>
            )}

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

      <RecentlyViewedProducts currentSlug={product.slug} />
    </>
  );
}
