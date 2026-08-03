import { Fragment } from 'react';
import type { Metadata } from 'next';
import { cmsApi, RSC_API_BASE } from '@/lib/api';
import type { Product, Category } from '@/lib/types';
import { HeroCarousel, type HeroContent } from '@/components/home/HeroCarousel';
import { UspMarquee, type UspContent } from '@/components/home/UspMarquee';
import { FeaturedProducts, type FeaturedContent } from '@/components/home/FeaturedProducts';
import { CategoryTiles, type CategoriesContent } from '@/components/home/CategoryTiles';
import { PromoBanners, type PromosContent } from '@/components/home/PromoBanners';
import { MeetTheBaker, type BakerContent } from '@/components/home/MeetTheBaker';
import { CaterSection, type CaterContent } from '@/components/home/CaterSection';
import { orderedSections, type HomeLayout, type SectionKey } from '@/lib/home-sections';
import { BAKERY_BASE, BUSINESS_ID, OG_IMAGE } from '@/lib/schema';

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

async function getCategories(): Promise<Category[]> {
  try {
    const res = await fetch(`${RSC_API_BASE}/categories`, {
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

function buildJsonLd(categories: Category[], featuredProducts: Product[]) {
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

async function getFeaturedProducts(): Promise<Product[]> {
  try {
    const res = await fetch(`${RSC_API_BASE}/products/featured`, {
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const c = await getHomeContent(locale);
  const title =
    c.seo?.title ?? 'Melting Moments Cakes — Brownie & Dessert Delivery Across the UAE';
  const description =
    c.seo?.description ??
    'Home bakery in Sharjah delivering fudgy brownies, gooey cookies, cookie melts, cakes and desserts to Dubai, Sharjah, Ajman and the rest of the UAE. Baked to order.';

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

  const [c, featuredProducts, categories] = await Promise.all([
    getHomeContent(locale),
    getFeaturedProducts(),
    getCategories(),
  ]);

  const jsonLd = buildJsonLd(categories, featuredProducts);
  const activeCategories = categories.filter(cat => cat.is_active);

  const sections: Record<SectionKey, React.ReactNode> = {
    hero: <HeroCarousel c={c.hero ?? {}} locale={locale} />,
    usps: <UspMarquee c={c.usps ?? {}} />,
    featured: (
      <FeaturedProducts products={featuredProducts} c={c.featured ?? {}} locale={locale} />
    ),
    categories: (
      <CategoryTiles c={c.categories ?? {}} categories={activeCategories} locale={locale} />
    ),
    promos: <PromoBanners c={c.promos ?? {}} locale={locale} />,
    baker: <MeetTheBaker c={c.baker ?? {}} locale={locale} />,
    cater: <CaterSection c={c.cater ?? {}} locale={locale} />,
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      {/* Scroll reveals start at opacity 0 and are un-hidden by an observer.
          With scripting off there is no observer, so opt out of the whole
          effect rather than serve an invisible page. */}
      <noscript>
        <style>{'.mm-reveal{opacity:1!important;animation:none!important}'}</style>
      </noscript>

      {orderedSections(c.layout).map(key => (
        <Fragment key={key}>{sections[key]}</Fragment>
      ))}
    </>
  );
}
