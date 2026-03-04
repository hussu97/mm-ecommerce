'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { use } from 'react';
import { productsApi } from '@/lib/api';
import type { Product } from '@/lib/types';
import { ProductForm } from '@/components/products/ProductForm';
import { Spinner } from '@/components/ui';

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
        <Link href="/products" className="text-gray-400 hover:text-primary transition-colors">
          <span className="material-icons text-[20px]">arrow_back</span>
        </Link>
        <div>
          <h1 className="font-display text-2xl text-gray-800">Edit Product</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">{product.name}</p>
        </div>
      </div>
      <ProductForm product={product} />
    </div>
  );
}
