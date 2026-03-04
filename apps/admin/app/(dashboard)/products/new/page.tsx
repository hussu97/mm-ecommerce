import type { Metadata } from 'next';
import Link from 'next/link';
import { ProductForm } from '@/components/products/ProductForm';

export const metadata: Metadata = { title: 'New Product' };

export default function NewProductPage() {
  return (
    <div>
      <div className="flex items-center gap-3 mb-6">
        <Link href="/products" className="text-gray-400 hover:text-primary transition-colors">
          <span className="material-icons text-[20px]">arrow_back</span>
        </Link>
        <h1 className="font-display text-2xl text-gray-800">New Product</h1>
      </div>
      <ProductForm />
    </div>
  );
}
