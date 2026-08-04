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

export function formatCurrency(amount: number | string): string {
  return `AED ${Number(amount).toFixed(2)}`;
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

/** Just the time, where the surrounding row already says which day. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-AE', {
    hour: 'numeric', minute: '2-digit', timeZone: SHOP_TZ,
  });
}
