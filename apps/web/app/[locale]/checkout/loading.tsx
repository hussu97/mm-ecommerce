export default function CheckoutLoading() {
  return (
    <div className="max-w-7xl mx-auto px-4 py-10">
      {/* Breadcrumb skeleton */}
      <div className="flex gap-2 mb-6">
        {[10, 2, 10, 2, 20].map((w, i) => (
          <div key={i} className={`h-3 w-${w} bg-gray-200 animate-pulse rounded-sm`} />
        ))}
      </div>

      {/* Title skeleton */}
      <div className="h-9 w-48 bg-gray-200 animate-pulse mb-6" />

      {/* Step indicator skeleton */}
      <div className="flex gap-4 mb-8">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-3 w-20 bg-gray-200 animate-pulse" />
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-10">
        {/* Form area */}
        <div className="lg:col-span-2 space-y-4">
          <div className="h-6 w-44 bg-gray-200 animate-pulse" />
          <div className="h-px bg-secondary/30" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="space-y-1">
              <div className="h-3 w-20 bg-gray-200 animate-pulse" />
              <div className="h-10 w-full bg-gray-200 animate-pulse" />
            </div>
          ))}
          <div className="h-12 w-full bg-gray-200 animate-pulse mt-4" />
        </div>

        {/* Summary sidebar */}
        <div className="lg:col-span-1">
          <div className="bg-gray-50 border border-gray-100 rounded-sm p-5 space-y-4">
            <div className="h-5 w-32 bg-gray-200 animate-pulse" />
            {Array.from({ length: 2 }).map((_, i) => (
              <div key={i} className="flex gap-3">
                <div className="w-12 h-12 bg-gray-200 animate-pulse rounded-sm shrink-0" />
                <div className="flex-1 space-y-1">
                  <div className="h-3 w-full bg-gray-200 animate-pulse" />
                  <div className="h-3 w-1/2 bg-gray-200 animate-pulse" />
                </div>
              </div>
            ))}
            <div className="h-px bg-gray-200" />
            <div className="h-4 w-full bg-gray-200 animate-pulse" />
          </div>
        </div>
      </div>
    </div>
  );
}
