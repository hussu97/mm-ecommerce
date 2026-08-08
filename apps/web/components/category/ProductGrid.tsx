import type { Product } from '@/lib/types';
import { ProductCard } from './ProductCard';
import { Icon } from '@/components/ui/Icon';

export function ProductGrid({ products }: { products: Product[] }) {
  if (products.length === 0) {
    return (
      <div className="text-center py-24">
        <Icon name="inventory_2" className="text-5xl text-secondary mb-4 block" />
        <p className="font-body text-gray-500 text-sm uppercase tracking-widest">
          No products available yet — check back soon!
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 lg:grid-cols-3 gap-x-3 gap-y-7 sm:gap-8">
      {products.map((product, i) => (
        // Two columns on a phone, three from `lg`, so the first four cards are
        // the ones that can be on screen when the page paints — and one of them
        // is the LCP element. Everything after stays lazy.
        <ProductCard key={product.id} product={product} priority={i < 4} />
      ))}
    </div>
  );
}
