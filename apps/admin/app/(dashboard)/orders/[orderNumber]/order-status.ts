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
 */
export const SETTLED_STATUSES: OrderStatus[] = ['cancelled'];

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
