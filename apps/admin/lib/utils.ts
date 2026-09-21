export function cn(...classes: (string | undefined | null | false)[]): string {
  return classes.filter(Boolean).join(' ');
}

/**
 * The faint row highlight every data table shares.
 *
 * A row lights up both on hover *and* while any control inside it holds focus
 * (`focus-within`), so on an inline-edit table — transfers/production, stock
 * counts, purchase orders — clicking into a cell's input tints the whole row
 * and it stays clear which line you are editing. Apply this to the body `<tr>`;
 * the shared `DataTable` already carries it, and the hand-rolled tables import
 * it so the behaviour reads the same everywhere.
 */
export const interactiveRowClass = 'transition-colors hover:bg-gray-50 focus-within:bg-gray-50';

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, '')
    .replace(/[\s_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/**
 * The one money formatter for the whole console: "AED 1,234.50".
 *
 * Locale-grouped via `Intl.NumberFormat` so four-digit totals stop reading as
 * "AED 12345.00". This replaced a second formatter (`money()` in the POS
 * `ResourcePage`) that printed "12,345.00 AED" — adjacent screens showed the
 * same dirham in two different shapes, so the POS side now shows this one.
 */
const AED = new Intl.NumberFormat('en-AE', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatCurrency(amount: number | string | null | undefined): string {
  return `AED ${AED.format(Number(amount ?? 0))}`;
}

/**
 * The money formatter for inventory COST figures: "AED 0.1706".
 *
 * Unit costs and FIFO valuations carry sub-cent precision (a raw material can
 * cost AED 0.0091 per gram), so a 2dp `formatCurrency` rounds them to "AED 0.01"
 * and the cost breakdown stops adding up. Costs therefore show four decimals.
 * Sales money — prices, order totals, payments, receipts — stays on
 * `formatCurrency` (2dp): this is only for the cost/valuation side.
 */
const AED_COST = new Intl.NumberFormat('en-AE', {
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

export function formatCost(amount: number | string | null | undefined): string {
  return `AED ${AED_COST.format(Number(amount ?? 0))}`;
}

/**
 * The one quantity formatter for the whole console: drops trailing zeros so a
 * stock figure reads "8.375" and "1", not "8.37500000".
 *
 * Inventory quantities are stored as high-scale numerics (the stock ledger is
 * `Numeric(20, 8)`, reports `Numeric(20, 6)`) and serialise to strings with the
 * full scale, so rendering the raw value fills every cell with trailing zeros.
 * The trim is lossless — a fixed-scale decimal only ever loses zeros — so this
 * shows the real figure, unlike a rounded `toFixed`. This replaced the local
 * `trimQty` in `RecipeEditor` so quantities read the same on every screen.
 */
export function formatQuantity(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  // A string from the API carries an exact fixed-scale decimal, so trim it as
  // text and stay lossless. A JS number is a computed figure (a running net, a
  // difference) that can carry float noise — round to the ledger's 8-place
  // scale first so the trim isn't defeated by "2.9999999999996".
  const s = typeof value === 'number' ? value.toFixed(8) : String(value);
  if (!s.includes('.')) return s;
  return s.replace(/0+$/, '').replace(/\.$/, '');
}

/**
 * The shop's clock, and the only one the admin ever shows.
 *
 * Everything is stored in UTC and every person reading this screen is standing
 * in the shop, so the browser's timezone is the wrong answer twice over: on a
 * laptop set to London a delivery at 00:30 Dubai reads as the previous day, and
 * whether a date is right becomes a property of whose machine is open.
 */
const SHOP_TZ = 'Asia/Dubai';

/* ------------------------------------------------------------------ *
 * Shop-time calendar dates.
 *
 * Every date range the console asks the API for is a calendar date in
 * Asia/Dubai, not in whoever's browser is open. Computing "today" from a plain
 * `new Date()` and `toISOString().slice(0,10)` reads the *browser's* UTC day, so
 * a laptop opened between 00:00 and 04:00 Dubai (still "yesterday" in UTC) sends
 * a range a whole day off — the reporting bug this exists to stop. We take the
 * shop's current Y-M-D, anchor it at UTC midnight, do the day arithmetic there
 * (DST-immune; Dubai has none, but the anchor keeps it honest), and serialise
 * back to the `YYYY-MM-DD` the API expects.
 * ------------------------------------------------------------------ */

/** A `Date` anchored at UTC midnight of the shop's *current* calendar day. */
export function shopTodayAnchor(): Date {
  // en-CA renders as YYYY-MM-DD, which splits without locale surprises.
  const ymd = new Intl.DateTimeFormat('en-CA', {
    timeZone: SHOP_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
  const [y, m, d] = ymd.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

/** `YYYY-MM-DD` for a UTC-anchored date. */
export function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** `d` shifted by whole days on its UTC anchor. */
export function addUtcDays(d: Date, days: number): Date {
  const next = new Date(d);
  next.setUTCDate(next.getUTCDate() + days);
  return next;
}

/** The shop's current calendar date as `YYYY-MM-DD` (Asia/Dubai). */
export function todayInShopTz(): string {
  return isoDay(shopTodayAnchor());
}

/** `YYYY-MM-DD`, `days` before the shop's today (Asia/Dubai). */
export function shopDaysAgo(days: number): string {
  return isoDay(addUtcDays(shopTodayAnchor(), -days));
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-AE', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: SHOP_TZ,
  });
}

/**
 * A date and the time of day, for the moments where the hour is the point —
 * when a rider collected, when a box was handed over.
 *
 * Worth having as well as `formatDate`: two Lalamove deliveries were recorded
 * four hours in the future for a week, and nobody saw it because the only place
 * those timestamps surfaced showed the date alone.
 */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-AE', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: 'numeric', minute: '2-digit', timeZone: SHOP_TZ,
  });
}

/**
 * How long ago, in the words somebody uses out loud — "40s ago", "3 min ago".
 *
 * For facts whose *age* is the point rather than their clock time. A driver's
 * position is the case this was written for: "as of 14:32" makes a reader do
 * the subtraction, and the whole reason the stamp is shown is that a distance
 * measured four minutes ago means something different from one measured now.
 *
 * Anything past an hour is given as a time instead. By then the number of
 * minutes has stopped being the useful form, and nothing that quotes this is
 * shown at all once it is that stale.
 */
export function formatTimeAgo(iso: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return formatTime(iso);
}

/**
 * How long ago, when the gap can be days — "2 min", "5 h", "4 days".
 *
 * `formatTimeAgo` gives a clock time past an hour, which is right for a driver
 * position nobody looks at once it is that stale and wrong for a terminal: a
 * till that has been off since Tuesday would read "14:32", which is not a fact
 * about Tuesday. This one keeps counting.
 *
 * No "ago" in the string, so a caller can put it after its own word — "seen
 * 4 days" reads better than "seen 4 days ago" once the label is already there.
 */
export function formatAge(iso: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h`;
  const days = Math.round(seconds / 86400);
  return `${days} ${days === 1 ? 'day' : 'days'}`;
}

/** `1` → `1st`. For "the 2nd driver on this order". */
export function ordinal(value: number): string {
  const suffix =
    value % 100 >= 11 && value % 100 <= 13
      ? 'th'
      : ['th', 'st', 'nd', 'rd'][value % 10] ?? 'th';
  return `${value}${suffix}`;
}

/** Just the time, where the surrounding row already says which day. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-AE', {
    hour: 'numeric', minute: '2-digit', timeZone: SHOP_TZ,
  });
}

/**
 * One CSV cell, safe to hand to a spreadsheet. Quotes the value and doubles any
 * embedded quote, AND neutralises formula injection: a value that begins with
 * `=`, `+`, `-`, `@`, a tab or a carriage return is prefixed with a single quote
 * so Excel/Sheets treats it as text rather than executing it (F-ADM-15). Use this
 * for every browser-built CSV — an item name or SKU is attacker-influenced data.
 */
export function csvCell(value: unknown): string {
  let text = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(text)) {
    text = `'${text}`;
  }
  return `"${text.replaceAll('"', '""')}"`;
}
