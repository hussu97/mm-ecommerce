'use client';

/**
 * The orders-list filter bar — the same filters the dashboard exposes, laid out
 * for a table: a date range, a search, and multi-select status and carrier chips
 * (the carriers with their logos, "like how statuses is"). State lives in the URL
 * via `useOrderFilters`, so a view survives a refresh and arrives pre-filtered
 * when a dashboard scorecard links here.
 */

import { Input } from '@/components/ui';
import { cn } from '@/lib/utils';
import { COURIER_OPTIONS } from '@/lib/couriers';
import { CourierMark } from '@/components/orders/CourierLogo';
import { DateRangePresets } from '@/components/orders/DateRangePresets';
import type { OrderFilters } from '@/lib/order-filters';
import { hasAnyFilter } from '@/lib/order-filters';

export const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'created', label: 'Created' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'arrived_at_pos', label: 'At the shop' },
  { value: 'packed', label: 'Packed' },
  { value: 'out_for_delivery', label: 'On the way' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'undelivered', label: 'Undelivered' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'payment_failed', label: 'Payment failed' },
  { value: 'refunded', label: 'Refunded' },
  // A disputed order renders its own status badge everywhere but could not be
  // filtered to — the one settled state missing from the chips (F-ADM-19).
  { value: 'disputed', label: 'Disputed' },
];

function Chip({
  on,
  onClick,
  children,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={cn(
        'inline-flex items-center gap-1.5 border px-2.5 py-1 text-xs font-body transition-colors',
        // A real thumb target on a phone; compact again at a desk.
        'min-h-[var(--tap-min)] md:min-h-0',
        on
          ? 'border-primary bg-primary/5 text-primary'
          : 'border-gray-200 text-gray-600 hover:border-gray-300',
      )}
    >
      {children}
    </button>
  );
}

export function OrderFilterBar({
  filters,
  search,
  onSearch,
  onPatch,
  onToggleStatus,
  onToggleCourier,
  onToggleBranch,
  onToggleLegalEntity,
  onToggleCategory,
  onClearAll,
  branchOptions,
  legalEntityOptions,
  categoryOptions,
}: {
  filters: OrderFilters;
  /** The live search box value (debounced into the URL by the parent). */
  search: string;
  onSearch: (v: string) => void;
  onPatch: (partial: Partial<OrderFilters>) => void;
  onToggleStatus: (v: string) => void;
  onToggleCourier: (v: string) => void;
  onToggleBranch: (v: string) => void;
  onToggleLegalEntity: (v: string) => void;
  onToggleCategory: (v: string) => void;
  onClearAll: () => void;
  branchOptions: { value: string; label: string }[];
  legalEntityOptions: { value: string; label: string }[];
  categoryOptions: { value: string; label: string }[];
}) {
  return (
    <div className="mb-4 space-y-3">
      <div>
        <span className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1.5">
          Quick range
        </span>
        <DateRangePresets filters={filters} onPatch={onPatch} />
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1">
            From
          </label>
          <input
            type="date"
            value={filters.from}
            max={filters.to || undefined}
            onChange={e => onPatch({ from: e.target.value })}
            className="px-3 h-10 border border-gray-300 bg-white text-sm font-body outline-none focus:border-primary"
          />
        </div>
        <div>
          <label className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1">
            To
          </label>
          <input
            type="date"
            value={filters.to}
            min={filters.from || undefined}
            onChange={e => onPatch({ to: e.target.value })}
            className="px-3 h-10 border border-gray-300 bg-white text-sm font-body outline-none focus:border-primary"
          />
        </div>
        <div className="flex-1 min-w-[16rem]">
          <label className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1">
            Search
          </label>
          <Input
            placeholder="Order #, ref, name, email, phone, product or SKU…"
            value={search}
            onChange={e => onSearch(e.target.value)}
          />
        </div>
        {hasAnyFilter(filters) && (
          <button
            onClick={onClearAll}
            className="h-10 px-3 text-xs font-body uppercase tracking-wider text-gray-500 border border-gray-300 hover:bg-gray-50 transition-colors"
          >
            Clear all
          </button>
        )}
      </div>

      <div>
        <span className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1.5">
          Status
        </span>
        <div className="flex flex-wrap gap-1.5">
          {STATUS_OPTIONS.map(s => (
            <Chip
              key={s.value}
              on={filters.statuses.includes(s.value)}
              onClick={() => onToggleStatus(s.value)}
            >
              {s.label}
            </Chip>
          ))}
        </div>
      </div>

      <div>
        <span className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1.5">
          Courier
        </span>
        <div className="flex flex-wrap gap-1.5">
          {COURIER_OPTIONS.map(c => (
            <Chip
              key={c.code}
              on={filters.couriers.includes(c.code)}
              onClick={() => onToggleCourier(c.code)}
            >
              <CourierMark code={c.code} size={16} />
              {c.label}
            </Chip>
          ))}
        </div>
      </div>

      {branchOptions.length > 0 && (
        <div>
          <span className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1.5">
            Branch
          </span>
          <div className="flex flex-wrap gap-1.5">
            {branchOptions.map(b => (
              <Chip
                key={b.value}
                on={filters.branches.includes(b.value)}
                onClick={() => onToggleBranch(b.value)}
              >
                {b.label}
              </Chip>
            ))}
          </div>
        </div>
      )}

      {legalEntityOptions.length > 0 && (
        <div>
          <span className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1.5">
            Legal entity
          </span>
          <div className="flex flex-wrap gap-1.5">
            {legalEntityOptions.map(e => (
              <Chip
                key={e.value}
                on={filters.legalEntities.includes(e.value)}
                onClick={() => onToggleLegalEntity(e.value)}
              >
                {e.label}
              </Chip>
            ))}
          </div>
        </div>
      )}

      {categoryOptions.length > 0 && (
        <div>
          <span className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1.5">
            Category
          </span>
          <div className="flex flex-wrap gap-1.5">
            {categoryOptions.map(c => (
              <Chip
                key={c.value}
                on={filters.categories.includes(c.value)}
                onClick={() => onToggleCategory(c.value)}
              >
                {c.label}
              </Chip>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
