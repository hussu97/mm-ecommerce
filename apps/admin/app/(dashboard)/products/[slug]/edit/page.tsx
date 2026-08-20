'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { use } from 'react';
import { productsApi } from '@/lib/api';
import type { Product } from '@/lib/types';
import { ProductForm } from '@/components/products/ProductForm';
import { Spinner } from '@/components/ui';
import { BranchStockPanel } from '@/components/products/BranchStock';

export default function EditProductPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const [product, setProduct] = useState<Product | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    productsApi.get(slug)
      .then(setProduct)
      .catch(() => setError('Product not found.'))
      .finally(() => setLoading(false));
  }, [slug]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Spinner />
      </div>
    );
  }

  if (error || !product) {
    return (
      <div className="text-center py-16">
        <p className="text-sm text-gray-500 font-body mb-4">{error || 'Not found.'}</p>
        <Link href="/products" className="text-xs text-primary hover:underline font-body">
          Back to Products
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-6">
        <Link href="/products" className="inline-flex items-center justify-center min-h-11 min-w-11 -ml-2 md:min-h-0 md:min-w-0 md:ml-0 text-gray-400 hover:text-primary transition-colors">
          <span className="material-icons text-[20px]">arrow_back</span>
        </Link>
        <div>
          <h1 className="font-display text-2xl text-gray-800">Edit Product</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">{product.name}</p>
        </div>
      </div>
      <ProductForm product={product} />

      {/*
        Below the form rather than inside it, because it is not part of the
        form: every control here writes immediately to one branch's row, while
        the form above is a draft nobody has saved yet. Folding branch stock
        into the same Save would mean a manager marking something out at one
        kitchen and losing it by navigating away.
      */}
      <section className="mt-8">
        <h2 className="font-display text-lg text-gray-800 mb-1">Branch stock</h2>
        <p className="text-xs font-body text-gray-400 mb-3">
          What each kitchen can make right now. The website shows a customer the
          catalogue of the branch that would bake their order, so a product off
          sale here is gone from that area&rsquo;s storefront. Saved as you press
          it — nothing here waits for Save above.
        </p>
        <BranchStockPanel productId={product.id} />
      </section>
    </div>
  );
}
