// Purchasing helpers shared by the PO screens.

import { holdsPermission } from './nav';
import type { PurchaseOrderMiscCategory } from './pos-api';

/** Holders see — and may use — admin-only misc categories (rent, salary…).
 *  The API enforces it; the console only hides what it would refuse. */
export const RESTRICTED_MISC_PERMISSION = 'inventory.purchase_orders.restricted_misc';

export function canSeeRestrictedMisc(
  user: { is_superadmin?: boolean; permissions?: string[] } | null | undefined,
): boolean {
  return holdsPermission(RESTRICTED_MISC_PERMISSION, user);
}

// ─── P&L placement of misc spend ──────────────────────────────────────────────

/** Which profit level a misc line's cost lands above. Unset means PC4. */
export type MiscPnlLevel = NonNullable<PurchaseOrderMiscCategory['pnl_level']>;

export const PNL_LEVEL_OPTIONS: { value: MiscPnlLevel; label: string }[] = [
  { value: 'pc1', label: 'PC1 · cost of goods (above PC1)' },
  { value: 'pc2', label: 'PC2 · above PC2 (with fees)' },
  { value: 'pc3', label: 'PC3 · above PC3 (with discounts)' },
  { value: 'pc4', label: 'PC4 · overhead below PC3' },
];

/** The P&L's sales channels, in its column order. */
export const PNL_CHANNEL_OPTIONS: { value: string; label: string }[] = [
  { value: 'counter', label: 'Counter' },
  { value: 'website_delivery', label: 'Website delivery' },
  { value: 'website_pickup', label: 'Store pickup' },
  { value: 'custom', label: 'Custom orders' },
  { value: 'talabat', label: 'Talabat' },
  { value: 'keeta', label: 'Keeta' },
  { value: 'noon_food', label: 'noon Food' },
  { value: 'deliveroo', label: 'Deliveroo' },
  { value: 'careem', label: 'Careem' },
];

/** `Talabat, Keeta` for channel codes; an unknown code shows as itself. */
export function pnlChannelsLabel(codes: string[]): string {
  return codes
    .map((c) => PNL_CHANNEL_OPTIONS.find((o) => o.value === c)?.label ?? c)
    .join(', ');
}

/** A category's placement as one line: `PC1 · Custom orders`, `PC4 · All channels`. */
export function pnlPlacementLabel(level: MiscPnlLevel | null | undefined, channels: string[]): string {
  return `${(level ?? 'pc4').toUpperCase()} · ${channels.length ? pnlChannelsLabel(channels) : 'All channels'}`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function parts(iso: string): [number, number, number] {
  const [y, m, d] = iso.split('-').map(Number);
  return [y, m, d];
}

function lastDay(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/**
 * A short label for a misc line's period: `Sep 2026` for one whole month,
 * `Jul – Sep 2026` / `Nov 2026 – Oct 2027` for whole months, else the days
 * (`26 Sep 2026`, `21 – 27 Sep 2026`, `28 Sep – 4 Oct 2026`).
 * Pure string work on the ISO dates — no time zone can shift them.
 */
export function periodLabel(from: string, to: string): string {
  const [fy, fm, fd] = parts(from);
  const [ty, tm, td] = parts(to);
  if (fd === 1 && td === lastDay(ty, tm)) {
    if (fy === ty && fm === tm) return `${MONTHS[fm - 1]} ${fy}`;
    if (fy === ty) return `${MONTHS[fm - 1]} – ${MONTHS[tm - 1]} ${ty}`;
    return `${MONTHS[fm - 1]} ${fy} – ${MONTHS[tm - 1]} ${ty}`;
  }
  if (from === to) return `${fd} ${MONTHS[fm - 1]} ${fy}`;
  if (fy === ty && fm === tm) return `${fd} – ${td} ${MONTHS[tm - 1]} ${ty}`;
  if (fy === ty) return `${fd} ${MONTHS[fm - 1]} – ${td} ${MONTHS[tm - 1]} ${ty}`;
  return `${fd} ${MONTHS[fm - 1]} ${fy} – ${td} ${MONTHS[tm - 1]} ${ty}`;
}

/** `YYYY-MM-DD` for a year / 1-based month / day. */
export function isoDate(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/** First and last day of a month, as ISO dates. */
export function monthStart(year: number, month: number): string {
  return isoDate(year, month, 1);
}
export function monthEnd(year: number, month: number): string {
  return isoDate(year, month, lastDay(year, month));
}

/** The Monday / Sunday of the ISO week holding *iso* (weeks start Monday). */
export function weekStart(iso: string): string {
  const [y, m, d] = parts(iso);
  const date = new Date(Date.UTC(y, m - 1, d));
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
  return date.toISOString().slice(0, 10);
}
export function weekEnd(iso: string): string {
  const [y, m, d] = parts(weekStart(iso));
  const date = new Date(Date.UTC(y, m - 1, d + 6));
  return date.toISOString().slice(0, 10);
}

export const MONTH_OPTIONS = MONTHS.map((label, i) => ({ value: String(i + 1), label }));
