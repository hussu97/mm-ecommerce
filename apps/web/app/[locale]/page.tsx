import { Fragment } from 'react';
import type { Metadata } from 'next';
import { cmsApi, RSC_API_BASE } from '@/lib/api';
// The same request-scoped fetch the locale layout uses for the nav bar, so one
// homepage render asks the API for the category list once rather than twice.
import { getCategories } from '@/lib/catalogue';
import { CACHE_TAGS, CONTENT_TTL } from '@/lib/cache-policy';
import type { AdvertisedPromo, Product, Category } from '@/lib/types';
import { DeliveryPromiseBanner } from '@/components/home/DeliveryPromiseBanner';
import { HeroCarousel, type HeroContent } from '@/components/home/HeroCarousel';
import { UspMarquee, type UspContent } from '@/components/home/UspMarquee';
import { FeaturedProducts, type FeaturedContent } from '@/components/home/FeaturedProducts';
import { CategoryTiles, type CategoriesContent } from '@/components/home/CategoryTiles';
import { PromoBanners, type PromosContent } from '@/components/home/PromoBanners';
import { MeetTheBaker, type BakerContent } from '@/components/home/MeetTheBaker';
import { CaterSection, type CaterContent } from '@/components/home/CaterSection';
import { PromoBanner } from '@/components/layout/PromoBanner';
import { orderedSections, type HomeLayout, type SectionKey } from '@/lib/home-sections';
import { BAKERY_BASE, BUSINESS_ID, OG_IMAGE } from '@/lib/schema';
import { fetchJson } from '@/lib/fetch-json';
import { getFeaturedPromo, isAdvertisable, offerHeadline, offerSentence } from '@/lib/offer';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

interface HomeContent {
  hero?: HeroContent;
  usps?: UspContent;
  featured?: FeaturedContent;
  categories?: CategoriesContent;
  promos?: PromosContent;
  baker?: BakerContent;
  cater?: CaterContent;
  layout?: HomeLayout;
  seo?: { title?: string; description?: string };
}

/**
 * The coupon as a schema.org `Offer`, or null when there is nothing to state.
 *
 * Every figure comes off the row, so retiring the campaign in the admin removes
 * this node on the next revalidation rather than leaving a dead code published
 * to every crawler that has ever read the page. `validThrough` is omitted
 * rather than guessed when the campaign is open-ended — an invented end date is
 * a worse claim than no end date.
 *
 * `discountCode` and `discount` are schema.org *pending* terms, and there is no
 * Google rich result for a site-wide coupon. This is not chasing one: consumers
 * that do not understand a term ignore it, the `makesOffer` edge on the bakery
 * is standard vocabulary, and the value is that an answer engine reading this
 * graph can state the offer correctly instead of inventing one.
 */
export function buildOffer(promo: AdvertisedPromo | null, locale: 'en' | 'ar' = 'en') {
  if (!isAdvertisable(promo)) return null;
  const percent = Number(promo.discount_value);
  return {
    '@type': 'Offer',
    '@id': `${SITE_URL}/#new-customer-offer`,
    // Stated in the language of the page carrying it. The `@id` is shared
    // across both, which is correct — it is one campaign — but the prose is
    // what a reader quotes, and quoting English at an Arabic page is how the
    // Arabic snippet ends up half in the wrong language.
    name: offerHeadline(promo, locale)!,
    // The same sentence `llms.txt` publishes, so the graph and the file an
    // answer engine reads cannot describe the campaign differently.
    description: offerSentence(promo, locale)!,
    url: `${SITE_URL}/${locale}/all-products`,
    discountCode: promo.code,
    discount: percent,
    priceCurrency: 'AED',
    seller: { '@id': BUSINESS_ID },
    ...(promo.valid_until ? { validThrough: promo.valid_until } : {}),
  };
}

function buildJsonLd(
  categories: Category[],
  featuredProducts: Product[],
  promo: AdvertisedPromo | null,
  locale: 'en' | 'ar',
) {
  const offer = buildOffer(promo, locale);
  // Group featured products by category for the Menu schema
  const categoryMap = new Map<string, { name: string; slug: string; products: Product[] }>();
  for (const cat of categories.filter(c => c.is_active)) {
    categoryMap.set(cat.id, { name: cat.name, slug: cat.slug, products: [] });
  }
  for (const p of featuredProducts) {
    if (p.category_id && categoryMap.has(p.category_id)) {
      categoryMap.get(p.category_id)!.products.push(p);
    }
  }

  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'WebSite',
        '@id': `${SITE_URL}/#website`,
        name: 'Melting Moments Cakes',
        url: SITE_URL,
        publisher: { '@id': BUSINESS_ID },
        inLanguage: ['en-AE', 'ar-AE'],
        potentialAction: {
          '@type': 'SearchAction',
          target: {
            '@type': 'EntryPoint',
            urlTemplate: `${SITE_URL}/en/search?q={search_term_string}`,
          },
          'query-input': 'required name=search_term_string',
        },
      },
      {
        ...BAKERY_BASE,
        description:
          'Home bakery in Sharjah delivering brownies, cookies, cookie melts, cakes and desserts across all seven emirates. Everything is baked to order.',
        hasMenu: `${SITE_URL}/en/all-products`,
        // The offer hangs off the bakery that makes it, so a reader that
        // resolves this entity gets the campaign with it rather than having to
        // notice a loose node in the graph.
        ...(offer ? { makesOffer: { '@id': offer['@id'] } } : {}),
        potentialAction: {
          '@type': 'OrderAction',
          target: {
            '@type': 'EntryPoint',
            urlTemplate: `${SITE_URL}/en/all-products`,
            actionPlatform: [
              'https://schema.org/DesktopWebPlatform',
              'https://schema.org/MobileWebPlatform',
            ],
          },
          deliveryMethod: ['https://schema.org/OnSitePickup', 'https://schema.org/ParcelService'],
        },
      },
      {
        '@type': 'Menu',
        name: 'Melting Moments Cakes Menu',
        hasMenuSection: [...categoryMap.values()]
          .filter(c => c.products.length > 0)
          .map(c => ({
            '@type': 'MenuSection',
            name: c.name,
            hasMenuItem: c.products.map(p => ({
              '@type': 'MenuItem',
              name: p.name,
              description: p.description ?? undefined,
              offers: {
                '@type': 'Offer',
                price: Number(p.base_price).toFixed(2),
                priceCurrency: 'AED',
              },
              url: `${SITE_URL}/en/${c.slug}/${p.slug}`,
            })),
          })),
      },
      // Last, and only when a campaign is actually running. The whole node
      // disappears with the campaign — there is no version of it that outlives
      // the coupon it describes.
      ...(offer ? [offer] : []),
    ],
  };
}

async function getHomeContent(locale: string): Promise<HomeContent> {
  try {
    const page = await cmsApi.getPage('home', locale);
    return page.content as HomeContent;
  } catch {
    return {};
  }
}

/**
 * The bestsellers rail. Throws on a broken API rather than quietly rendering an
 * empty rail — which, cached for a minute, is a homepage that has stopped
 * selling anything. See `lib/fetch-json.ts`.
 */
async function getFeaturedProducts(): Promise<Product[]> {
  const items = await fetchJson<Product[]>(`${RSC_API_BASE}/products/featured`, {
    next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
    signal: AbortSignal.timeout(8000),
  });
  return items ?? [];
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const [c, promo] = await Promise.all([getHomeContent(locale), getFeaturedPromo()]);
  const title =
    c.seo?.title ?? 'Melting Moments Cakes — Brownie & Dessert Delivery Across the UAE';
  const base =
    c.seo?.description ??
    'Home bakery in Sharjah delivering fudgy brownies, gooey cookies, cookie melts, cakes and desserts to Dubai, Sharjah, Ajman and the rest of the UAE. Baked to order.';

  // The live offer, appended to whatever the description already says.
  //
  // This is the line a search result shows and the line an answer engine is
  // most likely to quote back, so an offer absent from it is an offer that does
  // not exist as far as either is concerned. Appended rather than replacing,
  // because what the bakery sells is still the thing being described — and it
  // reverts on its own the moment the campaign ends, with no copy to go and
  // unwrite. Held to a length a SERP will actually show.
  //
  // **In the language of the page.** This shipped English-only and appended a
  // clause about "New customers" to an otherwise Arabic description, which is
  // precisely the string Google prints as the Arabic snippet. The CMS
  // description is per-locale; the offer has to be too.
  const lang = locale === 'ar' ? 'ar' : 'en';
  const headline = offerHeadline(promo, lang);
  const offerLine = headline
    ? lang === 'ar'
      ? ` للعملاء الجدد: ${headline} بكود ${promo!.code}.`
      : ` New customers: ${headline} with code ${promo!.code}.`
    : '';
  const description =
    offerLine && (base + offerLine).length <= 320 ? base + offerLine : base;

  return {
    title,
    description,
    alternates: {
      canonical: `${SITE_URL}/${locale}`,
      languages: {
        en: `${SITE_URL}/en`,
        ar: `${SITE_URL}/ar`,
        'x-default': `${SITE_URL}/en`,
      },
    },
    openGraph: {
      title: c.seo?.title ?? 'Melting Moments Cakes',
      description,
      images: [OG_IMAGE],
    },
  };
}

export default async function HomePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;

  const [c, featuredProducts, categories, promo] = await Promise.all([
    getHomeContent(locale),
    getFeaturedProducts(),
    getCategories(),
    getFeaturedPromo(),
  ]);

  const jsonLd = buildJsonLd(categories, featuredProducts, promo, locale === 'ar' ? 'ar' : 'en');
  const activeCategories = categories.filter(cat => cat.is_active);

  const sections: Record<SectionKey, React.ReactNode> = {
    hero: <HeroCarousel c={c.hero ?? {}} locale={locale} categories={activeCategories} />,
    usps: <UspMarquee c={c.usps ?? {}} />,
    featured: (
      <FeaturedProducts products={featuredProducts} c={c.featured ?? {}} locale={locale} />
    ),
    categories: (
      <CategoryTiles c={c.categories ?? {}} categories={activeCategories} locale={locale} />
    ),
    promos: <PromoBanners c={c.promos ?? {}} locale={locale} categories={activeCategories} />,
    baker: <MeetTheBaker c={c.baker ?? {}} locale={locale} />,
    cater: <CaterSection c={c.cater ?? {}} locale={locale} />,
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      {/* Above the hero, because it is the one thing on this page a shopper who
          has never bought here needs to know before they start looking. */}
      <PromoBanner promo={promo} />
      {/* Scroll reveals start at opacity 0 and are un-hidden by an observer.
          With scripting off there is no observer, so opt out of the whole
          effect rather than serve an invisible page. */}
      <noscript>
        <style>{'.mm-reveal{opacity:1!important;animation:none!important}'}</style>
      </noscript>

      {/* Above the hero, and outside the CMS section order on purpose: it is
          the answer to "do you deliver to me", which is the first question the
          page is asked and the one nobody should have to scroll for. */}
      <DeliveryPromiseBanner />

      {orderedSections(c.layout).map(key => (
        <Fragment key={key}>{sections[key]}</Fragment>
      ))}
    </>
  );
}
