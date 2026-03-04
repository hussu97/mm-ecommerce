import { ProductCardSkeleton } from '@/components/ui';

export default function CategoryLoading() {
  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      {/* Title skeleton */}
      <div className="h-8 w-52 bg-gray-200 animate-pulse mb-2" />
      <div className="h-px bg-secondary/40 mb-10" />

      {/* Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 sm:gap-8">
        {Array.from({ length: 6 }).map((_, i) => (
          <ProductCardSkeleton key={i} />
        ))}
      </div>
    </div>
  );
}
