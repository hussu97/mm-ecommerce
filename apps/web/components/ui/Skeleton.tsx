import { cn } from '@/lib/utils';

interface SkeletonProps {
  className?: string;
  rounded?: boolean;
  circle?: boolean;
}

export function Skeleton({ className, rounded = false, circle = false }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'animate-pulse bg-gray-200',
        circle ? 'rounded-full' : rounded ? 'rounded-sm' : '',
        className,
      )}
    />
  );
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn('h-3', i === lines - 1 ? 'w-3/4' : 'w-full')}
          rounded
        />
      ))}
    </div>
  );
}

export function ProductCardSkeleton() {
  return (
    <div className="bg-white border border-gray-100 rounded-sm overflow-hidden">
      <Skeleton className="w-full aspect-square" />
      <div className="p-4 space-y-3">
        <Skeleton className="h-4 w-3/4" rounded />
        <Skeleton className="h-3 w-1/2" rounded />
        <Skeleton className="h-8 w-full mt-2" rounded />
      </div>
    </div>
  );
}
