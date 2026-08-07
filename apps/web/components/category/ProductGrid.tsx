import type { ProductList } from '@/lib/analytics';
import type { Product } from '@/lib/types';
import { ProductCard } from './ProductCard';

export function ProductGrid({
  products,
  list = 'category',
}: {
  products: Product[];
  /** Which listing this grid is. Decides how `select_item` is attributed. */
  list?: ProductList;
}) {
  if (products.length === 0) {
    return (
      <div className="text-center py-24">
        <span className="material-icons text-5xl text-secondary mb-4 block">inventory_2</span>
        <p className="font-body text-gray-500 text-sm uppercase tracking-widest">
          No products available yet — check back soon!
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 lg:grid-cols-3 gap-x-3 gap-y-7 sm:gap-8">
      {products.map((product, i) => (
        <ProductCard key={product.id} product={product} list={list} position={i} />
      ))}
    </div>
  );
}
