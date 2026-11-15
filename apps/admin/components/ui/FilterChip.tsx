'use client';

import { cn } from '@/lib/utils';

/** A list's quick filter with its count — "Needs attention 3". The count is
 *  tinted when it is non-zero and the filter means trouble. */
export function FilterChip({
  active,
  count,
  tone,
  onClick,
  children,
}: {
  active: boolean;
  count: number;
  tone?: 'red' | 'amber' | 'gray';
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'inline-flex shrink-0 items-center gap-2 whitespace-nowrap border px-3 py-1.5 text-xs font-body transition-colors',
        active
          ? 'border-primary bg-primary text-white'
          : 'border-gray-200 bg-white text-gray-700 hover:border-primary/50',
      )}
    >
      {children}
      <span
        className={cn(
          'min-w-[1.25rem] rounded-full px-1.5 text-center text-[11px]',
          active
            ? 'bg-white/20 text-white'
            : count > 0 && tone === 'red'
              ? 'bg-red-100 text-red-700'
              : count > 0 && tone === 'amber'
                ? 'bg-amber-100 text-amber-800'
                : 'bg-gray-100 text-gray-500',
        )}
      >
        {count}
      </span>
    </button>
  );
}
