import { countAge, type CountAgeBand } from '@/lib/count-age';
import { formatDateTime } from '@/lib/utils';

// Quiet when it is fine, loud when it is not: a fresh count is just a green dot
// and its age; due and overdue carry a tint so they stand out down a column;
// never-counted is a dashed outline — an absence, not a value.
const STYLES: Record<CountAgeBand, { chip: string; dot: string }> = {
  fresh: { chip: 'text-emerald-700', dot: 'bg-emerald-500' },
  due: { chip: 'bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200', dot: 'bg-amber-500' },
  overdue: { chip: 'bg-red-50 text-red-700 ring-1 ring-inset ring-red-200', dot: 'bg-red-500' },
  never: { chip: 'text-gray-500 border border-dashed border-gray-300', dot: 'border border-gray-400' },
};

export function CountAgeChip({ at, className = '' }: { at: string | null | undefined; className?: string }) {
  const age = countAge(at);
  const style = STYLES[age.band];
  const title = at ? `Last physically counted ${formatDateTime(at)}` : 'Never physically counted here';
  return (
    <span
      title={title}
      aria-label={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-1.5 py-px text-[10px] font-medium leading-tight tabular-nums ${style.chip} ${className}`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${style.dot}`} />
      {age.label}
    </span>
  );
}
