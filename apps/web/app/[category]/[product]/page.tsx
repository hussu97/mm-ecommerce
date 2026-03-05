import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Image from 'next/image';
import { Breadcrumb } from '@/components/ui';
import { ProductDetailATC } from './ProductDetailATC';
import type { Product } from '@/lib/types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';

async function getProduct(slug: string): Promise<Product | null> {
  const res = await fetch(`${API_BASE}/products/${slug}`, { next: { revalidate: 300 } });
  if (!res.ok) return null;
  return res.json();
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ category: string; product: string }>;
}): Promise<Metadata> {
  const { product: slug } = await params;
  const product = await getProduct(slug);
  if (!product) return {};

  const description =
    product.description ??
    `Order ${product.name} — handcrafted with love in the UAE by Melting Moments Cakes.`;
  const ogImages = product.image_urls?.length
    ? product.image_urls.slice(0, 1).map(url => ({ url, alt: product.name }))
    : [{ url: '/images/logos/color_logo.jpeg', alt: 'Melting Moments Cakes' }];

  return {
    title: product.name,
    description,
    openGraph: {
      title: `${product.name} | Melting Moments Cakes`,
      description,
      images: ogImages,
    },
  };
}

export default async function ProductDetailPage({
  params,
}: {
  params: Promise<{ category: string; product: string }>;
}) {
  const { category: categorySlug, product: productSlug } = await params;
  const product = await getProduct(productSlug);
  if (!product) notFound();

  const categoryName = product.category?.name ?? categorySlug;
  const mainImage = product.image_urls?.[0];
  const galleryImages = product.image_urls ?? [];

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Product',
    name: product.name,
    description: product.description ?? undefined,
    image: galleryImages,
    url: `https://meltingmomentscakes.com/${categorySlug}/${productSlug}`,
    offers: product.variants.map(v => ({
      '@type': 'Offer',
      name: v.name,
      price: Number(v.price).toFixed(2),
      priceCurrency: 'AED',
      availability: v.stock_quantity > 0 ? 'https://schema.org/InStock' : 'https://schema.org/OutOfStock',
    })),
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
            { label: 'Home', href: '/' },
            { label: categoryName, href: `/${categorySlug}` },
            { label: product.name },
          ]}
        />

        <div className="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-12">
          {/* Images */}
          <div className="flex flex-col gap-3">
            <div className="relative aspect-square overflow-hidden bg-[#f9f5f0]">
              {mainImage ? (
                <Image
                  src={mainImage}
                  alt={product.name}
                  fill
                  sizes="(max-width: 1024px) 100vw, 50vw"
                  className="object-cover"
                  priority
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center">
                  <span className="material-icons text-8xl text-secondary">cake</span>
                </div>
              )}
            </div>

            {/* Thumbnails */}
            {galleryImages.length > 1 && (
              <div className="flex gap-2 overflow-x-auto">
                {galleryImages.map((url, i) => (
                  <div key={i} className="relative w-20 h-20 shrink-0 overflow-hidden bg-[#f9f5f0]">
                    <Image
                      src={url}
                      alt={`${product.name} image ${i + 1}`}
                      fill
                      sizes="80px"
                      className="object-cover"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Details + ATC */}
          <div className="flex flex-col gap-6">
            <div>
              <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest mb-3">
                {product.name}
              </h1>
              <div className="h-px bg-secondary/40" />
            </div>

            {product.description && (
              <p className="font-body text-sm text-gray-600 leading-relaxed">
                {product.description}
              </p>
            )}

            {/* Interactive: variant selector, qty, ATC */}
            <ProductDetailATC product={product} />
          </div>
        </div>
      </div>
    </>
  );
}
