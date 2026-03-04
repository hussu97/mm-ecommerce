'use client';

import { useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useCart } from '@/lib/cart-context';
import type { Product } from '@/lib/types';

function ProductCard({ product }: { product: Product }) {
  const { addItem } = useCart();
  const [adding, setAdding] = useState(false);

  const defaultVariant = product.variants?.[0];
  const price = defaultVariant?.price ?? product.base_price;
  const image = product.image_urls?.[0];
  const categorySlug = product.category?.slug ?? 'products';

  const handleAdd = async () => {
    if (!defaultVariant) return;
    setAdding(true);
    try {
      await addItem(defaultVariant.id, 1);
    } finally {
      setAdding(false);
    }
  };

  return (
    <article className="group flex flex-col flex-shrink-0 w-64 sm:w-auto">
      <Link
        href={`/${categorySlug}/${product.slug}`}
        className="block relative aspect-square overflow-hidden bg-[#f9f5f0]"
      >
        {image ? (
          <Image
            src={image}
            alt={product.name}
            fill
            sizes="(max-width: 640px) 256px, (max-width: 1024px) 33vw, 25vw"
            className="object-cover group-hover:scale-105 transition-transform duration-500"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <span className="material-icons text-5xl text-secondary">cake</span>
          </div>
        )}
      </Link>

      <div className="pt-3 flex flex-col gap-2">
        <Link
          href={`/${categorySlug}/${product.slug}`}
          className="font-display text-sm text-gray-800 hover:text-primary transition-colors leading-snug"
        >
          {product.name}
        </Link>
        <div className="h-px bg-secondary/40" />
        <div className="flex items-center justify-between gap-2">
          <span className="font-body text-sm font-medium text-primary">
            {Number(price).toFixed(2)} AED
          </span>
          <button
            onClick={handleAdd}
            disabled={adding || !defaultVariant}
            className="text-[10px] font-body uppercase tracking-widest px-3 py-1.5 border border-primary text-primary hover:bg-primary hover:text-white transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {adding ? '...' : 'Add'}
          </button>
        </div>
      </div>
    </article>
  );
}

export function FeaturedProducts({ products }: { products: Product[] }) {
  if (products.length === 0) return null;

  return (
    <section aria-label="Featured products" className="py-16 bg-white">
      <div className="max-w-7xl mx-auto px-4">

        <div className="flex items-baseline justify-between mb-8">
          <h2 className="font-display text-2xl sm:text-3xl text-gray-800 uppercase tracking-widest">
            Our Bestsellers
          </h2>
          <Link
            href="/brownies"
            className="text-xs font-body text-primary uppercase tracking-widest hover:underline hidden sm:inline"
          >
            View All
          </Link>
        </div>

        {/* Horizontal scroll on mobile, grid on sm+ */}
        <div className="flex gap-5 overflow-x-auto pb-4 sm:pb-0 sm:grid sm:grid-cols-2 lg:grid-cols-4 -mx-4 px-4 sm:mx-0 sm:px-0 snap-x snap-mandatory sm:snap-none">
          {products.map((product) => (
            <div key={product.id} className="snap-start">
              <ProductCard product={product} />
            </div>
          ))}
        </div>

        <div className="mt-8 text-center sm:hidden">
          <Link
            href="/brownies"
            className="text-xs font-body text-primary uppercase tracking-widest hover:underline"
          >
            View All
          </Link>
        </div>

      </div>
    </section>
  );
}
