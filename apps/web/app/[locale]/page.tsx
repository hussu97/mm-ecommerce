import type { Metadata } from 'next';
import { cmsApi, API_BASE } from '@/lib/api';
import type { Product, Category } from '@/lib/types';
import { HeroSection, type HeroContent } from '@/components/home/HeroSection';
import { FeaturedProducts, type FeaturedContent } from '@/components/home/FeaturedProducts';
import { MeetTheBaker, type BakerContent } from '@/components/home/MeetTheBaker';
import { CaterSection, type CaterContent } from '@/components/home/CaterSection';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

interface HomeContent {
  hero?: HeroContent;
  featured?: FeaturedContent;
  baker?: BakerContent;
  cater?: CaterContent;
  seo?: { title?: string; description?: string };
}

async function getCategories(): Promise<Category[]> {
  try {
    const res = await fetch(`${API_BASE}/categories`, {
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
        '@type': 'Organization',
        name: 'Melting Moments Cakes',
        url: SITE_URL,
        logo: `${SITE_URL}/images/logos/color_logo.jpeg`,
        sameAs: ['https://www.instagram.com/meltingmomentscakes'],
      },
      {
        '@type': 'WebSite',
        name: 'Melting Moments Cakes',
        url: SITE_URL,
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
        '@type': 'Bakery',
        '@id': SITE_URL,
        name: 'Melting Moments Cakes',
        description:
          'Artisanal bakery delivering handcrafted brownies, cookies and desserts across the UAE',
        url: SITE_URL,
        logo: `${SITE_URL}/images/logos/color_logo.jpeg`,
        image: `${SITE_URL}/images/logos/color_logo.jpeg`,
        telephone: '+971503687757',
        address: {
          '@type': 'PostalAddress',
          addressCountry: 'AE',
          addressRegion: 'Sharjah',
        },
        geo: {
          '@type': 'GeoCoordinates',
          latitude: 25.3304,
          longitude: 55.3710,
        },
        currenciesAccepted: 'AED',
        priceRange: 'AED 15–200',
        servesCuisine: ['Brownies', 'Cookies', 'Desserts', 'Artisanal Baked Goods'],
        paymentAccepted: 'Cash, Credit Card',
        areaServed: 'AE',
        hasMenu: `${SITE_URL}/en/all-products`,
        sameAs: ['https://www.instagram.com/meltingmomentscakes'],
        openingHoursSpecification: [
          { '@type': 'OpeningHoursSpecification', dayOfWeek: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'], opens: '08:00', closes: '23:30' },
          { '@type': 'OpeningHoursSpecification', dayOfWeek: 'Sunday', opens: '15:00', closes: '23:30' },
        ],
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
    const res = await fetch(`${API_BASE}/products/featured`, {
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
  const title = c.seo?.title ?? 'Melting Moments Cakes — Artisanal Bakery in UAE';
  const description =
    c.seo?.description ??
    'Handcrafted brownies, cookies, cookie melts, and desserts delivered across the UAE. Made with 100% love by Fatema Abbasi.';

  return {
    title,
    description,
    alternates: {
      canonical: `${SITE_URL}/${locale}`,
      languages: { en: `${SITE_URL}/en`, ar: `${SITE_URL}/ar` },
    },
    openGraph: {
      title: c.seo?.title ?? 'Melting Moments Cakes',
      description,
      images: [{ url: '/images/logos/color_logo.jpeg' }],
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

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      <HeroSection c={c.hero ?? {}} locale={locale} />
      <FeaturedProducts products={featuredProducts} c={c.featured ?? {}} locale={locale} />
      <MeetTheBaker c={c.baker ?? {}} locale={locale} />
      <CaterSection c={c.cater ?? {}} />
    </>
  );
}
