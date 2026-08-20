export function cn(...classes: (string | undefined | null | false)[]): string {
  return classes.filter(Boolean).join(' ');
}

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
 * The shop's clock, and the only one the admin ever shows.
 *
 * Everything is stored in UTC and every person reading this screen is standing
 * in the shop, so the browser's timezone is the wrong answer twice over: on a
 * laptop set to London a delivery at 00:30 Dubai reads as the previous day, and
 * whether a date is right becomes a property of whose machine is open.
 */
const SHOP_TZ = 'Asia/Dubai';

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
