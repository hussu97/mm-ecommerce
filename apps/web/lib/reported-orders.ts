'use client';

/**
 * Dedupe for the `order_completed` analytics event.
 *
 * The confirmation page fires that event from an effect that derives entirely
 * from what `ordersApi.get` returns, and that answer is the same every time
 * the page is opened for a given order — a refresh, the back button, a
 * bookmarked link. Umami has no idea those are the same purchase, so every
 * extra look inflated the revenue and order-count numbers read off it.
 *
 * A capped list in localStorage remembers which order numbers have already
 * reported once *in this browser*. It is not authoritative across devices —
 * a customer refreshing on their phone and then opening the same link on a
 * laptop still reports twice — it only catches the overwhelmingly common
 * case of hitting the same URL again.
 */

const KEY = 'mm_order_completed_reported';

/**
 * How many order numbers to remember. A ring rather than an ever-growing set:
 * nobody needs the dedupe to still recognise an order from a year ago, and an
 * unbounded list is one more way to hit localStorage's quota.
 */
const MAX_ENTRIES = 50;

function read(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function write(list: string[]) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* private mode / quota — dedupe silently stops working, event still fires */
  }
}

export const reportedOrders = {
  /**
   * True the first time this order number is asked about in this browser;
   * false every time after. Marks it as seen as a side effect of asking, so
   * the check and the marking are one localStorage round trip and can never
   * disagree with each other.
   *
   * Never throws: a localStorage failure (private mode, quota, a disabled
   * store) is swallowed and answered as "first seen", so the worst case is
   * the old behaviour — the event still fires — not a broken confirmation
   * page.
   */
  markIfFirstSeen(orderNumber: string): boolean {
    try {
      const list = read();
      if (list.includes(orderNumber)) return false;
      list.push(orderNumber);
      write(list.slice(-MAX_ENTRIES));
      return true;
    } catch {
      return true;
    }
  },
};
