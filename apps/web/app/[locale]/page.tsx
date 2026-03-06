import type { Metadata } from 'next';
import type { Product } from '@/lib/types';
import { HeroSection } from '@/components/home/HeroSection';
import { FeaturedProducts } from '@/components/home/FeaturedProducts';
import { MeetTheBaker } from '@/components/home/MeetTheBaker';
import { CaterSection } from '@/components/home/CaterSection';

export const metadata: Metadata = {
  title: 'Melting Moments Cakes — Artisanal Bakery in UAE',
  description:
    'Handcrafted brownies, cookies, cookie melts, and desserts delivered across the UAE. Made with 100% love by Fatema Abbasi.',
  openGraph: {
    title: 'Melting Moments Cakes',
    description: 'Handcrafted brownies, cookies and desserts — made with 100% love in the UAE',
    images: [{ url: '/images/logos/color_logo.jpeg' }],
  },
};

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

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

async function getFeaturedProducts(): Promise<Product[]> {
  try {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
    const res = await fetch(`${apiUrl}/api/v1/products/featured`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export default async function HomePage() {
  const featuredProducts = await getFeaturedProducts();

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(JSON_LD) }}
      />
      <HeroSection />
      <FeaturedProducts products={featuredProducts} />
      <MeetTheBaker />
      <CaterSection />
    </>
  );
}
