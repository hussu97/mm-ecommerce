export default function CartLoading() {
  return (
    <div className="max-w-7xl mx-auto px-4 py-10">
      {/* Breadcrumb skeleton */}
      <div className="flex gap-2 mb-6">
        <div className="h-3 w-10 bg-gray-200 animate-pulse rounded-sm" />
        <div className="h-3 w-2 bg-gray-200 animate-pulse rounded-sm" />
        <div className="h-3 w-10 bg-gray-200 animate-pulse rounded-sm" />
      </div>

      {/* Title skeleton */}
      <div className="h-9 w-40 bg-gray-200 animate-pulse mb-2" />
      <div className="h-px bg-secondary/40 mb-8" />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-10">
        {/* Cart items */}
        <div className="lg:col-span-2 space-y-0">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className={`flex gap-4 py-6 ${i > 0 ? 'border-t border-gray-100' : ''}`}>
              <div className="w-24 h-24 sm:w-28 sm:h-28 shrink-0 bg-gray-200 animate-pulse rounded-sm" />
              <div className="flex-1 space-y-2">
                <div className="h-4 w-3/4 bg-gray-200 animate-pulse" />
                <div className="h-3 w-1/4 bg-gray-200 animate-pulse" />
                <div className="h-8 w-28 bg-gray-200 animate-pulse mt-4" />
              </div>
            </div>
          ))}
        </div>

        {/* Summary sidebar */}
        <div className="lg:col-span-1">
          <div className="bg-gray-50 border border-gray-100 rounded-sm p-6 space-y-4">
            <div className="h-5 w-32 bg-gray-200 animate-pulse" />
            <div className="space-y-2">
              <div className="h-4 w-full bg-gray-200 animate-pulse" />
              <div className="h-4 w-full bg-gray-200 animate-pulse" />
            </div>
            <div className="h-px bg-gray-200" />
            <div className="h-4 w-full bg-gray-200 animate-pulse" />
            <div className="h-12 w-full bg-gray-200 animate-pulse" />
          </div>
        </div>
      </div>
    </div>
  );
}
