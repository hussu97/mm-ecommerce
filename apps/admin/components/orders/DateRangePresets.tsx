'use client';

/**
 * A row of quick date-range chips (Today, Yesterday, L7D, This month, Last
 * month, L30D) shared by the dashboard and the orders list. Each chip writes an
 * inclusive `{ from, to }` into the shared URL filter model via `onPatch`, so
 * the whole page re-filters to that window and the view stays shareable. The
 * chip whose range matches the current filters is highlighted; clicking it
 * again clears the range (back to the live day / all time).
 */

import { cn } from '@/lib/utils';
import { DATE_PRESETS, activePresetKey, type OrderFilters } from '@/lib/order-filters';

export function DateRangePresets({
  filters,
  onPatch,
}: {
  filters: OrderFilters;
  onPatch: (partial: Partial<OrderFilters>) => void;
}) {
  const active = activePresetKey(filters);

  return (
    <div className="flex flex-wrap gap-1.5">
      {DATE_PRESETS.map(p => {
        const on = active === p.key;
        return (
          <button
            key={p.key}
            type="button"
            onClick={() => (on ? onPatch({ from: '', to: '' }) : onPatch(p.range()))}
            aria-pressed={on}
            className={cn(
              'inline-flex items-center border px-2.5 py-1 text-xs font-body transition-colors',
              on
                ? 'border-primary bg-primary/5 text-primary'
                : 'border-gray-200 text-gray-600 hover:border-gray-300',
            )}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}
