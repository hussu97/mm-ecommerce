'use client';

import { cn } from '@/lib/utils';

/**
 * The full-width filter row under a list header, mirroring
 * `components/orders/OrderFilterBar`: a wrapping row of controls the caller
 * supplies, plus a "Clear all" that appears only when something is set. Kept
 * presentational — the caller owns the filter state (see `useUrlFilters`) and
 * passes `hasAny` / `onClear`.
 */
export function FilterBar({
  children,
  hasAny,
  onClear,
  className,
}: {
  children: React.ReactNode;
  hasAny?: boolean;
  onClear?: () => void;
  className?: string;
}) {
  return (
    <div className={cn('mb-4 flex flex-wrap items-end gap-2', className)}>
      {children}
      {hasAny && onClear && (
        <button
          type="button"
          onClick={onClear}
          className="text-xs font-body text-gray-500 underline underline-offset-2 hover:text-primary hover:no-underline min-h-[var(--tap-min)] md:min-h-0"
        >
          Clear all
        </button>
      )}
    </div>
  );
}
