/**
 * How long since an item was last physically counted at a branch, banded so
 * the console can colour it and filter on it.
 *
 * The stamp is `InventoryLevel.last_counted_at` (and the same figure on a
 * report line): set only when someone actually counted the item — a POS report
 * row that was filled in, or an admin stock audit. A row the shop skipped
 * leaves it alone, so an old or missing stamp means nobody has looked.
 *
 * Ages are whole calendar days in the shop's time zone (Asia/Dubai): a count
 * at 11pm is "today" until midnight there and "1d" after, whatever the
 * browser's own clock says.
 */

export type CountAgeBand = 'fresh' | 'due' | 'overdue' | 'never';

/** Counted within this many days → fresh; beyond → due. */
export const COUNT_FRESH_MAX_DAYS = 3;
/** Counted within this many days → due; beyond → overdue. */
export const COUNT_DUE_MAX_DAYS = 7;

const SHOP_TZ = 'Asia/Dubai';
const shopDay = new Intl.DateTimeFormat('en-CA', {
  timeZone: SHOP_TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

function dayNumber(d: Date): number {
  const [y, m, day] = shopDay.format(d).split('-').map(Number);
  return Math.floor(Date.UTC(y, m - 1, day) / 86_400_000);
}

/** Whole shop-calendar days since `iso`, or null when it never was. */
export function countAgeDays(iso: string | null | undefined, now: Date = new Date()): number | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  return Math.max(0, dayNumber(now) - dayNumber(at));
}

export function countAgeBand(days: number | null): CountAgeBand {
  if (days == null) return 'never';
  if (days <= COUNT_FRESH_MAX_DAYS) return 'fresh';
  if (days <= COUNT_DUE_MAX_DAYS) return 'due';
  return 'overdue';
}

export function countAgeLabel(days: number | null): string {
  if (days == null) return 'never';
  if (days === 0) return 'today';
  return `${days}d`;
}

export interface CountAge {
  days: number | null;
  band: CountAgeBand;
  label: string;
}

export function countAge(iso: string | null | undefined, now: Date = new Date()): CountAge {
  const days = countAgeDays(iso, now);
  return { days, band: countAgeBand(days), label: countAgeLabel(days) };
}

/** The newer of two stamps — a branch with several warehouses was last counted
 *  when any of them was. */
export function latestStamp(a: string | null | undefined, b: string | null | undefined): string | null {
  if (!a) return b ?? null;
  if (!b) return a;
  return new Date(a).getTime() >= new Date(b).getTime() ? a : b;
}

/** The "Last counted" filter: '' = any; 'fresh' = counted in the last
 *  3 days; 'over:N' = not counted in more than N days (never included);
 *  'never' = never counted. */
export type CountFilter = '' | 'fresh' | 'over:3' | 'over:7' | 'over:14' | 'over:30' | 'never';

export const COUNT_FILTER_OPTIONS: { value: CountFilter; label: string }[] = [
  { value: '', label: 'Any count date' },
  { value: 'fresh', label: `Counted in last ${COUNT_FRESH_MAX_DAYS} days` },
  { value: 'over:3', label: 'Not counted in 3+ days' },
  { value: 'over:7', label: 'Not counted in 7+ days' },
  { value: 'over:14', label: 'Not counted in 14+ days' },
  { value: 'over:30', label: 'Not counted in 30+ days' },
  { value: 'never', label: 'Never counted' },
];

export function matchesCountFilter(
  filter: CountFilter,
  iso: string | null | undefined,
  now: Date = new Date(),
): boolean {
  if (!filter) return true;
  const days = countAgeDays(iso, now);
  if (filter === 'never') return days == null;
  if (filter === 'fresh') return days != null && days <= COUNT_FRESH_MAX_DAYS;
  const over = Number(filter.slice('over:'.length));
  return days == null || days > over;
}
