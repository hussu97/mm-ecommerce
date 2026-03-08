import type { Metadata } from 'next';
import { cmsApi, API_BASE } from '@/lib/api';
import type { Product } from '@/lib/types';
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

const JSON_LD = {
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
    },
    {
      '@type': 'LocalBusiness',
      '@id': SITE_URL,
      name: 'Melting Moments Cakes',
      description:
        'Artisanal bakery delivering handcrafted brownies, cookies and desserts across the UAE',
      address: {
        '@type': 'PostalAddress',
        addressCountry: 'AE',
        addressRegion: 'Dubai',
      },
      currenciesAccepted: 'AED',
      areaServed: 'AE',
    },
  ],
};

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

  const [c, featuredProducts] = await Promise.all([
    getHomeContent(locale),
    getFeaturedProducts(),
  ]);

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(JSON_LD) }}
      />
      <HeroSection c={c.hero ?? {}} locale={locale} />
      <FeaturedProducts products={featuredProducts} c={c.featured ?? {}} locale={locale} />
      <MeetTheBaker c={c.baker ?? {}} locale={locale} />
      <CaterSection c={c.cater ?? {}} />
    </>
  );
}
