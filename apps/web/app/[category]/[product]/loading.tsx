export default function ProductDetailLoading() {
  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      {/* Breadcrumb skeleton */}
      <div className="flex gap-2 mb-6">
        {[10, 2, 20, 2, 32].map((w, i) => (
          <div key={i} className={`h-3 bg-gray-200 animate-pulse rounded-sm`} style={{ width: `${w * 4}px` }} />
        ))}
      </div>

      <div className="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-12">
        {/* Image skeleton */}
        <div className="flex flex-col gap-3">
          <div className="aspect-square bg-gray-200 animate-pulse" />
          <div className="flex gap-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="w-20 h-20 bg-gray-200 animate-pulse shrink-0" />
            ))}
          </div>
        </div>

        {/* Details skeleton */}
        <div className="flex flex-col gap-6">
          <div>
            <div className="h-10 w-3/4 bg-gray-200 animate-pulse mb-3" />
            <div className="h-px bg-secondary/40" />
          </div>
          <div className="space-y-2">
            <div className="h-4 w-full bg-gray-200 animate-pulse" />
            <div className="h-4 w-5/6 bg-gray-200 animate-pulse" />
            <div className="h-4 w-4/5 bg-gray-200 animate-pulse" />
          </div>
          <div className="h-8 w-24 bg-gray-200 animate-pulse" />
          <div className="h-12 w-full bg-gray-200 animate-pulse mt-4" />
        </div>
      </div>
    </div>
  );
}
