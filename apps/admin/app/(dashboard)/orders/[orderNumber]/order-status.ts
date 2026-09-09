/**
 * The order-status vocabulary the detail screen renders: which statuses are
 * steps, which are endings, which may be moved to, and how each one reads.
 *
 * Split out of `page.tsx` so the three components below it can share the maps
 * without importing the page.
 */

import type { Order, OrderStatus } from '@/lib/types';
import { formatDateTime } from '@/lib/utils';

/**
 * Orders that are not going anywhere, whatever the delivery row still says.
 *
 * `undelivered` was here and is not any more. A rider bringing the box back is
 * not a decision to stop selling somebody a cake: it is paid for, it exists,
 * and the shop can try again — with the same courier, a different one, or its
 * own car. Cancelling is what ends an order, and cancelling is what refunds it.
 *
 * Mirrors `_SETTLED_STATUSES` in `app/api/v1/orders.py`, which is what actually
 * enforces it — and which also refuses anything already refunded, so the
 * undelivered orders written while it *was* an ending stay where they are.
 *
 * The full set, not just `cancelled`: this had listed only `cancelled` while the
 * server settles `cancelled`/`refunded`/`disputed` (and anything with money
 * already refunded), so the three courier buttons showed on a refunded order and
 * could only ever 409 (F-ADM-9). Use `isSettled` — not this list alone — anywhere
 * a button is gated, so the `refunded_amount` half is covered too. The test in
 * `conventions.test.ts` parses the Python and fails if the two drift apart.
 */
export const SETTLED_STATUSES: OrderStatus[] = ['cancelled', 'refunded', 'disputed'];

/**
 * Whether the order is over, so no courier action should be offered on it.
 *
 * Mirrors `_assert_still_going_somewhere` exactly: a settled status, OR any
 * money already refunded — an order refunded by hand in a gateway keeps a live
 * status but is just as finished, and the server refuses a dispatch on it.
 */
export function isSettled(order: Pick<Order, 'status' | 'refunded_amount'>): boolean {
  return (
    SETTLED_STATUSES.includes(order.status as OrderStatus) ||
    Number(order.refunded_amount ?? 0) > 0
  );
}

/**
 * Where an order may be standing and still be handed to a different courier.
 *
 * Mirrors `MOVABLE_STATUSES` in `fulfilment_reassignment`, which is what
 * actually enforces it — this only decides whether to show the door. The API
 * asks a great deal more (a rider already holding the box, a driver on the way,
 * money already refunded), and answers with a sentence, which is why the dialog
 * opens on statuses where the move may still turn out to be refused.
 */
export const MOVABLE_STATUSES: OrderStatus[] = [
  'confirmed',
  'arrived_at_pos',
  'packed',
  'undelivered',
];

/**
 * The statuses ANY source may be cancelled from — the states `VALID_TRANSITIONS`
 * maps to `cancelled` (order_lifecycle.py). `packed` is deliberately NOT here:
 * the map closes it, and only a website or marketplace order reopens it through
 * the source hatch below. (`payment_failed` is cancellable server-side too but
 * has its own retry/void flow on this screen, so the Cancel button leaves it be.)
 */
const CANCELLABLE_FROM: OrderStatus[] = ['created', 'confirmed', 'arrived_at_pos'];

/**
 * Sources whose `packed` order may still be cancelled — the counterpart of
 * `ONLINE_CANCELLABLE_FROM` / `AGGREGATOR_CANCELLABLE_FROM`. A cashier order is
 * absent on purpose: there is no `CASHIER_CANCELLABLE_FROM`, so a packed counter
 * order is not cancellable, and offering the button only earned a 409 (F-ADM-16).
 */
const PACKED_CANCELLABLE_SOURCES = new Set(['online', 'aggregator']);

/**
 * Whether to offer "Cancel Order", mirroring what `update_status` + `transition`
 * will actually accept. It is a function of BOTH status and source, because the
 * one interesting case — a `packed` order — turns on where it came from.
 * `conventions.test.ts` holds this in step with the Python hatches.
 */
export function canCancel(order: Pick<Order, 'status' | 'source'>): boolean {
  if (CANCELLABLE_FROM.includes(order.status as OrderStatus)) return true;
  if (order.status === 'packed') {
    return order.source != null && PACKED_CANCELLABLE_SOURCES.has(order.source);
  }
  return false;
}

export const STATUS_STEPS: OrderStatus[] = [
  'created',
  'confirmed',
  'arrived_at_pos',
  'packed',
  'out_for_delivery',
  'delivered',
];

/**
 * When this order reached a given step, or '' if it never did.
 *
 * One source: the order's own status history. It used to be four separate
 * lookups — `created_at` for the first step and three columns on
 * `order_deliveries` for the rest — and those three are courier telemetry, so
 * a pickup order, a third-party zone or an order somebody walked through by
 * hand filled in exactly one of the five and left the others blank. `confirmed`
 * had no source at all and was documented as unanswerable.
 *
 * Still blank rather than guessed where a step genuinely never happened: an
 * order that went straight from packed to delivered has no `out_for_delivery`
 * row, and inventing one would be worse than the gap.
 */
export function stampFor(order: Order, step: OrderStatus): string {
  const hit = order.status_history?.find(s => s.status === step);
  return hit ? formatDateTime(hit.at) : '';
}

/**
 * The promise, at the precision it was made at.
 *
 * `day` and `day_by` are a date and nothing else — a third party's van is not
 * on our schedule, and printing an hour would borrow a precision we do not
 * have. `exact` is a record of something that already happened rather than a
 * promise, so it is not shown as one.
 */
export function promisedFor(order: Order): string | null {
  const f = order.fulfilment;
  if (!f?.estimated_at || !f.precision || f.precision === 'exact') return null;
  const at = new Date(f.estimated_at);
  const date = at.toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: 'Asia/Dubai',
  });
  if (f.precision === 'day') return date;
  const time = at.toLocaleTimeString('en-GB', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: 'Asia/Dubai',
  });
  return f.precision === 'day_by' ? `${date}, before ${time}` : `${date}, ${time}`;
}

/**
 * The address, in the boxes the customer typed it into.
 *
 * Order and wording follow `address_format._PARTS` and the checkout's own
 * labels, so the admin, the rider's stop, the ticket and the confirmation email
 * are all describing one address the same way. `unit_number` leads for the
 * reason it leads there: a map pin gets somebody to the building and the flat
 * number is what finishes the delivery.
 *
 * `label` is last and is the customer's own word for the place — "Home",
 * "Office". It is not part of the address but it is often the fastest way for
 * somebody on the phone to know which one they are looking at.
 */
export const ADDRESS_FIELDS: { key: string; label: string }[] = [
  { key: 'unit_number', label: 'Flat / villa / office' },
  { key: 'address_line_1', label: 'Address' },
  { key: 'address_line_2', label: 'Directions' },
  { key: 'city', label: 'City' },
  { key: 'label', label: 'Saved as' },
];

/** First and last name, however many of those the snapshot actually has. */
export function recipientName(snapshot: Record<string, string>): string {
  return [snapshot.first_name, snapshot.last_name]
    .map(part => (part ?? '').trim())
    .filter(Boolean)
    .join(' ');
}

/**
 * One address, however the source shaped it.
 *
 * A website order's `shipping_address_snapshot` uses `unit_number` /
 * `address_line_1` / `city` (what `ADDRESS_FIELDS` lists). A promoted marketplace
 * order carries the marketplace's OWN keys instead — Keeta `{address, building,
 * unit, house}`, Noon `{street, area, city, lat, lng}`, Careem `{street, building,
 * number, city, area, nickname, lat, lng}` — so the fixed website key list matched
 * nothing and the whole address rendered BLANK on the one screen a dispatcher opens
 * when a rider can't find the door. This coalesces each display line from every key
 * a channel might use for it, so the same panel renders every order type.
 *
 * Coordinates come as `latitude`/`longitude` (website, decimal) or `lat`/`lng`
 * (marketplaces) — and Noon sends them as scaled integers (`253337438` = 25.3337438).
 * A real UAE lat/lng is within ±180, so any larger magnitude is a scaled int → /1e7.
 */
export interface NormalizedAddress {
  rows: { label: string; value: string }[];
  lat: number | null;
  lng: number | null;
}

function _firstValue(
  snapshot: Record<string, unknown> | null | undefined,
  keys: string[],
): string | null {
  for (const key of keys) {
    const raw = snapshot?.[key];
    if (raw != null && String(raw).trim()) return String(raw).trim();
  }
  return null;
}

function _coord(value: unknown): number | null {
  if (value == null || value === '') return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return null;
  return Math.abs(n) > 180 ? n / 1e7 : n;
}

export function normalizeAddressSnapshot(
  snapshot: Record<string, unknown> | null | undefined,
): NormalizedAddress {
  if (!snapshot) return { rows: [], lat: null, lng: null };
  const rows: { label: string; value: string }[] = [];
  const add = (label: string, keys: string[]) => {
    const value = _firstValue(snapshot, keys);
    if (value) rows.push({ label, value });
  };
  // Flat/villa leads — a pin finds the building, the unit finishes the delivery.
  add('Flat / villa / office', ['unit_number', 'number', 'unit']);
  add('Address', ['address_line_1', 'street', 'address']);
  add('Building / directions', ['address_line_2', 'building', 'house']);
  add('Area', ['area']);
  add('City', ['city']);
  add('Saved as', ['label', 'nickname']);
  return {
    rows,
    lat: _coord(snapshot.latitude ?? snapshot.lat),
    lng: _coord(snapshot.longitude ?? snapshot.lng),
  };
}

export const STATUS_LABEL: Record<OrderStatus, string> = {
  created: 'created',
  confirmed: 'confirmed',
  arrived_at_pos: 'at the shop',
  packed: 'packed',
  out_for_delivery: 'on the way',
  delivered: 'delivered',
  undelivered: 'undelivered',
  cancelled: 'cancelled',
  payment_failed: 'payment failed',
  refunded: 'refunded',
  disputed: 'disputed',
};

/** The friendly text for a GrubOps cancellation reason.
 *
 * The value is stored as GrubOps spells it — a screaming-snake code
 * (`TOO_BUSY`, `ITEM_OUT_OF_STOCK`) on the common path, or a free-text fallback
 * when a cancellation carried its reason only in the history description. A code
 * is title-cased into a sentence; anything already sentence-shaped is shown
 * as-is, so an unrecognised or free-text reason is never mangled. */
export function humanizeCancelReason(reason: string): string {
  const trimmed = reason.trim();
  if (!/^[A-Z0-9_]+$/.test(trimmed)) return trimmed;
  const spaced = trimmed.replace(/_/g, ' ').toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export const STATUS_VARIANT: Record<OrderStatus, 'warning' | 'info' | 'success' | 'danger'> = {
  created: 'warning',
  confirmed: 'info',
  arrived_at_pos: 'info',
  packed: 'info',
  out_for_delivery: 'info',
  delivered: 'success',
  undelivered: 'danger',
  cancelled: 'danger',
  payment_failed: 'danger',
  refunded: 'warning',
  disputed: 'danger',
};
