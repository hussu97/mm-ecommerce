'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { ordersApi, ApiError } from '@/lib/api';
import type { LalamoveQuote, Order, OrderDelivery, OrderEconomics, OrderStatus } from '@/lib/types';
import { Badge, Button } from '@/components/ui';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { cn, formatCurrency, formatDateTime, formatTimeAgo, ordinal } from '@/lib/utils';

/**
 * Orders that are not going anywhere, whatever the delivery row still says.
 *
 * `undelivered` belongs here and did not used to: it is cancellation after the
 * box was made, and the API now treats it as an ending — no second van, no
 * status check, only a refund conversation. Mirrors `_SETTLED_STATUSES` in
 * `app/api/v1/orders.py`, which is what actually enforces it.
 */
const SETTLED_STATUSES: OrderStatus[] = ['cancelled', 'undelivered'];

const STATUS_STEPS: OrderStatus[] = [
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
function stampFor(order: Order, step: OrderStatus): string {
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
function promisedFor(order: Order): string | null {
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
const ADDRESS_FIELDS: { key: string; label: string }[] = [
  { key: 'unit_number', label: 'Flat / villa / office' },
  { key: 'address_line_1', label: 'Address' },
  { key: 'address_line_2', label: 'Directions' },
  { key: 'city', label: 'City' },
  { key: 'label', label: 'Saved as' },
];

/** First and last name, however many of those the snapshot actually has. */
function recipientName(snapshot: Record<string, string>): string {
  return [snapshot.first_name, snapshot.last_name]
    .map(part => (part ?? '').trim())
    .filter(Boolean)
    .join(' ');
}

const STATUS_LABEL: Record<OrderStatus, string> = {
  created: 'created',
  confirmed: 'confirmed',
  arrived_at_pos: 'at the shop',
  packed: 'packed',
  out_for_delivery: 'on the way',
  delivered: 'delivered',
  undelivered: 'undelivered',
  cancelled: 'cancelled',
};

const STATUS_VARIANT: Record<OrderStatus, 'warning' | 'info' | 'success' | 'danger'> = {
  created: 'warning',
  confirmed: 'info',
  arrived_at_pos: 'info',
  packed: 'info',
  out_for_delivery: 'info',
  delivered: 'success',
  undelivered: 'danger',
  cancelled: 'danger',
};

export default function OrderDetailPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const { orderNumber } = useParams<{ orderNumber: string }>();
  const [order, setOrder] = useState<Order | null>(null);
  const [delivery, setDelivery] = useState<OrderDelivery | null>(null);
  // What the shop kept. Admin-only and its own request, so a screen that fails
  // to load it still shows the order — the economics are context, not the page.
  const [economics, setEconomics] = useState<OrderEconomics | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  // The Lalamove quote awaiting a yes. Non-null means the dialog is open; the
  // price in it is the price that will be booked, and nothing else.
  const [quote, setQuote] = useState<LalamoveQuote | null>(null);
  // Set when a quote lapsed and a fresh one replaced it mid-dialog, so the
  // second confirm is visibly a second decision rather than the same click
  // going through at a different number.
  const [quoteExpired, setQuoteExpired] = useState(false);

  useEffect(() => {
    ordersApi.get(orderNumber)
      .then(o => { setOrder(o); setNotes(o.admin_notes ?? ''); })
      .catch(() => setError('Order not found.'))
      .finally(() => setLoading(false));
  }, [orderNumber]);

  // A fulfilment record is only ever opened for delivery orders, so asking for
  // one on a pickup order — every POS check included — is a guaranteed 404.
  // Delivery orders placed before fulfilment was tracked still 404, hence the
  // catch: that one is genuinely expected and not worth logging.
  const isDeliveryOrder = order?.delivery_method === 'delivery';

  const loadDelivery = useCallback(() => {
    if (!isDeliveryOrder) return;
    ordersApi.getDelivery(orderNumber)
      .then(setDelivery)
      .catch(err => { if (!(err instanceof ApiError && err.status === 404)) console.error(err); });
  }, [orderNumber, isDeliveryOrder]);

  useEffect(() => { loadDelivery(); }, [loadDelivery]);

  // ── while somebody is on their way ────────────────────────────────────────
  //
  // A distance is only useful if it moves. Fetched once, the card would say
  // "~4.2 km away" for as long as the tab stayed open, and the number would go
  // from helpful to actively misleading without changing.
  //
  // Only while there is a driver still coming to the branch — the one state in
  // which the figure exists at all — so an order sitting on somebody's second
  // monitor all afternoon is not polling for a delivery that finished at noon.
  // Half a minute, against a position the server refreshes each minute.
  const isDriverInbound = delivery?.driver_distance_km !== null && delivery !== null;
  useEffect(() => {
    if (!isDriverInbound) return;
    const timer = setInterval(loadDelivery, 30_000);
    return () => clearInterval(timer);
  }, [isDriverInbound, loadDelivery]);

  // Reloaded alongside the order rather than once: a refund, a re-dispatch or a
  // courier finally invoicing all move the net, and a stale figure here is
  // worse than none — somebody would price against it.
  useEffect(() => {
    ordersApi.getEconomics(orderNumber).then(setEconomics).catch(() => setEconomics(null));
  }, [orderNumber, order?.status, order?.refunded_amount, delivery?.cost_total]);

  async function updateStatus(newStatus: OrderStatus) {
    if (!order) return;
    // Cancelling goes through on the first click — it is the one status the
    // counter changes while the customer is still on the phone. Every other
    // move still asks.
    if (newStatus !== 'cancelled' && !(await confirmDialog(
      // Correcting a settled order is not the same act as advancing a live
      // one, and the difference is money: both endings refund automatically,
      // and marking one delivered afterwards does not take the refund back.
      // Say so here rather than let somebody find it in a reconciliation.
      SETTLED_STATUSES.includes(order.status as OrderStatus)
        ? {
            title: 'Correct this order',
            message:
              `${order.order_number} is recorded as ` +
              `"${STATUS_LABEL[order.status as OrderStatus]}". Marking it delivered ` +
              'corrects that record. Any refund already issued stays issued — ' +
              'check it before continuing.',
            confirmLabel: 'Mark delivered',
          }
        : {
            title: 'Change status',
            message: `Set order to "${STATUS_LABEL[newStatus]}"?`,
            confirmLabel: 'Change status',
          },
    ))) return;
    setActionLoading(true);
    try {
      const updated = await ordersApi.updateStatus(orderNumber, newStatus, notes || undefined);
      setOrder(updated);
      loadDelivery();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function redispatch() {
    if (!(await confirmDialog({
      title: 'Book courier again',
      message: 'Book the courier again for this order?',
      confirmLabel: 'Book courier',
    }))) return;
    setActionLoading(true);
    try {
      setDelivery(await ordersApi.dispatchDelivery(orderNumber));
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function quoteLalamove() {
    setActionLoading(true);
    setQuoteExpired(false);
    try {
      setQuote(await ordersApi.quoteLalamove(orderNumber));
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function assignLalamove() {
    if (!quote) return;
    setActionLoading(true);
    try {
      setDelivery(await ordersApi.assignLalamove(orderNumber, quote.quotation_id));
      // The order itself does not move — it stays `packed` until a rider
      // collects — but its history has, and the stepper reads that.
      setOrder(await ordersApi.get(orderNumber));
      setQuote(null);
      setQuoteExpired(false);
    } catch (err) {
      // A lapsed quotation comes back as a 409 carrying the current price.
      // Showing it and requiring another click is the whole point of the two
      // steps: nothing gets booked at a number nobody agreed to.
      const fresh = (err as ApiError)?.detail as { quote?: LalamoveQuote } | undefined;
      if (err instanceof ApiError && err.status === 409 && fresh?.quote) {
        setQuote(fresh.quote);
        setQuoteExpired(true);
      } else {
        toast.error((err as Error).message);
        setQuote(null);
      }
    } finally {
      setActionLoading(false);
    }
  }

  async function refreshCourier() {
    setActionLoading(true);
    try {
      setDelivery(await ordersApi.refreshDelivery(orderNumber));
      const fresh = await ordersApi.get(orderNumber);
      // The status may have moved with it — a pull that finds "delivered" walks
      // the order there, and leaving the header showing "packed" would make the
      // refresh look like it did nothing.
      setOrder(fresh);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function saveNotes() {
    if (!order) return;
    setActionLoading(true);
    try {
      const updated = await ordersApi.updateStatus(orderNumber, order.status, notes);
      setOrder(updated);
      toast.success('Notes saved.');
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error || !order) {
    return (
      <div className="text-sm text-red-500 font-body">{error || 'Order not found.'}</div>
    );
  }

  const isCancelled = order.status === 'cancelled';
  const isUndelivered = order.status === 'undelivered';
  const currentStepIdx = STATUS_STEPS.indexOf(order.status as OrderStatus);
  const promisedLabel = promisedFor(order);
  // Built here rather than in the JSX so the guard and the URL stay together:
  // a missing pin renders no button rather than a link to the middle of the sea.
  const snapshot = order.shipping_address_snapshot;
  const mapsHref =
    snapshot?.latitude && snapshot?.longitude
      ? `https://www.google.com/maps/search/?api=1&query=${snapshot.latitude},${snapshot.longitude}`
      : null;

  return (
    <div className="max-w-3xl">
      {quote && (
        <AssignLalamoveDialog
          quote={quote}
          expired={quoteExpired}
          busy={actionLoading}
          onConfirm={assignLalamove}
          onCancel={() => {
            setQuote(null);
            setQuoteExpired(false);
          }}
        />
      )}

      {/* Back + header */}
      <div className="flex items-center gap-3 mb-6">
        <Link href="/orders" className="inline-flex items-center justify-center min-h-11 min-w-11 -ml-2 md:min-h-0 md:min-w-0 md:ml-0 text-gray-400 hover:text-primary transition-colors">
          <span className="material-icons text-[20px]">arrow_back</span>
        </Link>
        <div className="flex-1">
          <h1 className="font-display text-xl text-gray-800">{order.order_number}</h1>
          <p className="text-xs text-gray-400 font-body">{formatDateTime(order.created_at)}</p>
        </div>
        <Badge variant={STATUS_VARIANT[order.status]}>{order.status}</Badge>
      </div>

      {/* A rider got there and came back with the box. Said before the
          timeline, because the timeline shows a journey this order has
          stepped out of. */}
      {isUndelivered && (
        <div className="bg-red-50 border border-red-200 p-4 mb-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-red-500 mb-1">Undelivered</p>
          <p className="text-sm font-body text-red-800">
            A rider reached the address and could not hand the order over. It is paid for
            and still ours to deliver — re-dispatch it below, or cancel and refund.
          </p>
        </div>
      )}

      {/* Status timeline */}
      {!isCancelled && !isUndelivered && (
        <div className="bg-white border border-gray-200 p-4 mb-4">
          <div className="flex items-baseline justify-between mb-3">
            <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">Status</p>
            {/* The promise, rendered at the precision it was made at. A
                third-party order has a date and no hour, and printing one would
                borrow a precision belonging to somebody else's van. */}
            {promisedLabel && (
              <p className="text-[11px] font-body text-gray-500">
                Estimated delivery{' '}
                <span className="text-gray-800">{promisedLabel}</span>
              </p>
            )}
          </div>
          <div className="flex items-center gap-0">
            {STATUS_STEPS.map((step, idx) => {
              const done = currentStepIdx >= idx;
              return (
                <div key={step} className="flex items-center flex-1">
                  <div className="flex flex-col items-center">
                    <div className={cn(
                      'w-7 h-7 rounded-full flex items-center justify-center text-xs font-body transition-colors',
                      done ? 'bg-primary text-white' : 'bg-gray-100 text-gray-400'
                    )}>
                      {done ? <span className="material-icons text-[14px]">check</span> : idx + 1}
                    </div>
                    <span className={cn('text-[10px] mt-1 font-body capitalize', done ? 'text-primary' : 'text-gray-400')}>
                      {STATUS_LABEL[step]}
                    </span>
                    {/* When it actually happened. A tick tells you an order got
                        somewhere; the stamp tells you when, which is the
                        question anyone opening this page at 9pm is asking. */}
                    <span className="text-[9px] mt-0.5 font-body text-gray-400 text-center leading-tight min-h-[1.2em]">
                      {stampFor(order, step)}
                    </span>
                  </div>
                  {idx < STATUS_STEPS.length - 1 && (
                    <div className={cn('flex-1 h-0.5 mx-1 mb-4', done && currentStepIdx > idx ? 'bg-primary' : 'bg-gray-100')} />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex gap-2 mb-4">
        {order.status === 'created' && (
          <Button size="sm" onClick={() => updateStatus('confirmed')} loading={actionLoading}>
            <span className="material-icons text-[14px]">check_circle</span>
            Confirm
          </Button>
        )}
        {order.status === 'confirmed' && (
          <Button size="sm" onClick={() => updateStatus('packed')} loading={actionLoading}>
            <span className="material-icons text-[14px]">inventory</span>
            Mark Packed
          </Button>
        )}
        {order.status === 'packed' && (
          <Button size="sm" onClick={() => updateStatus('out_for_delivery')} loading={actionLoading}>
            <span className="material-icons text-[14px]">local_shipping</span>
            Mark On The Way
          </Button>
        )}
        {/* The last of these two is a correction, not a step: `undelivered`
            and `cancelled` are endings, and the API opens the way back out of
            them for this screen alone (`ADMIN_RECOVERABLE`) — a courier's late
            "delivered" push must not resurrect an order the shop wrote off,
            but a person who knows the customer has the cake may say so. */}
        {(order.status === 'packed' ||
          order.status === 'out_for_delivery' ||
          isUndelivered ||
          isCancelled) && (
          <Button size="sm" onClick={() => updateStatus('delivered')} loading={actionLoading}>
            <span className="material-icons text-[14px]">done_all</span>
            {isUndelivered || isCancelled ? 'Correct To Delivered' : 'Mark Delivered'}
          </Button>
        )}
        {/* A courier reports this itself. The button is for the times nobody
            did — a third-party zone, or a driver who phoned the shop. */}
        {(order.status === 'packed' || order.status === 'out_for_delivery') && (
          <Button variant="danger" size="sm" onClick={() => updateStatus('undelivered')} loading={actionLoading}>
            <span className="material-icons text-[14px]">report_problem</span>
            Mark Undelivered
          </Button>
        )}
        {/* "Return To Packed" and cancelling an undelivered order used to sit
            here. Both were left behind when `undelivered` became an ending:
            the API refuses either, so the buttons could only ever produce a
            red toast. What follows an undelivered order is the refund it has
            already started, or the correction above. */}
        {(order.status === 'created' || order.status === 'confirmed') && (
          <Button variant="danger" size="sm" onClick={() => updateStatus('cancelled')} loading={actionLoading}>
            <span className="material-icons text-[14px]">cancel</span>
            Cancel Order
          </Button>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
        {/* Customer info */}
        <div className="bg-white border border-gray-200 p-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-2">Customer</p>
          <p className="text-sm font-body text-gray-800">{order.email}</p>
          {snapshot && (
            <div className="mt-2 text-xs font-body text-gray-500">
              <p className="text-gray-800">{recipientName(snapshot)}</p>
              <p>{snapshot.phone}</p>
              {/* Broken out rather than stacked into a paragraph. Every one of
                  these is a field the customer typed into its own box, and
                  running them together is how `unit_number` — the flat, the
                  villa, the office — came to be invisible on the one screen
                  somebody opens when a driver rings to say they cannot find
                  the door. `address_format.one_line` fixed the same bug in the
                  emails, the ticket and both couriers; this page was the fifth
                  copy and the last one still dropping it. */}
              <dl className="mt-2 space-y-1.5">
                {ADDRESS_FIELDS.map(({ key, label }) => {
                  const value = snapshot[key];
                  if (!value) return null;
                  return (
                    <div key={key}>
                      <dt className="text-[10px] uppercase tracking-wider text-gray-400">{label}</dt>
                      <dd className="text-gray-700">{value}</dd>
                    </div>
                  );
                })}
              </dl>
              {/* The pin, not the typed address. A UAE address line is often
                  unsearchable — "villa 12, behind the mosque" is a real one —
                  and the coordinates are what the courier is sent to. */}
              {mapsHref && (
                <a
                  href={mapsHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-3 inline-flex items-center gap-1.5 border border-gray-300 px-2.5 py-1.5 text-[11px] font-body text-gray-700 hover:border-primary hover:text-primary transition-colors"
                >
                  <span className="material-icons text-[14px]">place</span>
                  Open the pin in Google Maps
                </a>
              )}
            </div>
          )}
        </div>

        {/* Delivery + Payment */}
        <div className="bg-white border border-gray-200 p-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-2">Delivery & Payment</p>
          <dl className="text-xs font-body space-y-1">
            <div className="flex justify-between">
              <dt className="text-gray-500">Method</dt>
              <dd className="text-gray-700 capitalize">{order.delivery_method}</dd>
            </div>
            {/* Two lines, because they are two facts. "Card" is what the
                customer chose; the gateway is which processor settled it, and
                that is the one worth seeing when reconciling a payout or
                chasing a payment that a failover moved. A cash order has no
                gateway, so the second line stays off it. */}
            <div className="flex justify-between">
              <dt className="text-gray-500">Payment</dt>
              <dd className="text-gray-700 capitalize">{order.payment_method ?? '—'}</dd>
            </div>
            {order.payment_method !== 'cod' && (
              <div className="flex justify-between">
                <dt className="text-gray-500">Gateway</dt>
                <dd className="text-gray-700 capitalize">{order.payment_provider ?? '—'}</dd>
              </div>
            )}
            {order.promo_code_used && (
              <div className="flex justify-between">
                <dt className="text-gray-500">Promo</dt>
                <dd className="text-gray-700">{order.promo_code_used}</dd>
              </div>
            )}
          </dl>
        </div>
      </div>

      {delivery && (
        <DeliveryPanel
          delivery={delivery}
          busy={actionLoading}
          onRedispatch={redispatch}
          onAssignLalamove={quoteLalamove}
          canAssignLalamove={order.status === 'packed'}
          isSettled={SETTLED_STATUSES.includes(order.status)}
          onRefresh={refreshCourier}
        />
      )}

      {/* Items */}
      <div className="bg-white border border-gray-200 mb-4">
        <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 px-4 pt-4 pb-2">Items</p>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Product</th>
              <th className="px-4 py-2 text-center text-[11px] font-body uppercase tracking-widest text-gray-400">Qty</th>
              <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">Unit</th>
              <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">Total</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {order.items.map(item => (
              <tr key={item.id}>
                <td className="px-4 py-2.5">
                  <div className="text-xs font-body text-gray-800">{item.product_name}</div>
                  <div className="text-[11px] font-body text-gray-400">{item.product_sku}</div>
                  {/*
                    Each option carries its own count, and the count is the
                    whole content of a mixed box: "Fudge Brownie, Lindor
                    Brownie" describes a box of six no better than a box of two.
                    Printed for every option rather than only the plural ones —
                    beside a 5x, a bare name reads as an unknown quantity rather
                    than as one.
                  */}
                  {item.selected_options_snapshot && item.selected_options_snapshot.length > 0 && (
                    <div className="text-[11px] font-body text-gray-400 mt-0.5">
                      {item.selected_options_snapshot.map((o, i) => (
                        <span key={i}>{i > 0 ? ', ' : ''}{o.quantity ?? 1} &times; {o.option_name}{o.option_price > 0 ? ` (+${o.option_price.toFixed(2)})` : ''}</span>
                      ))}
                    </div>
                  )}
                  {/*
                    Louder than the options above it, and quoted. This is the
                    line somebody copies onto a card by hand — it has to be
                    legible and its edges have to be obvious, so a trailing
                    space or a deliberate lower-case name survives the reading.
                  */}
                  {item.personalisation_note && (
                    <div className="mt-1 border-s-2 border-primary/40 ps-2" dir="auto">
                      <div className="text-[10px] font-body uppercase tracking-widest text-gray-400">Handwritten note</div>
                      <div className="text-xs font-body text-gray-800 whitespace-pre-wrap">&ldquo;{item.personalisation_note}&rdquo;</div>
                    </div>
                  )}
                </td>
                <td className="px-4 py-2.5 text-center text-xs font-body text-gray-600">{item.quantity}</td>
                <td className="px-4 py-2.5 text-right text-xs font-body text-gray-600">{formatCurrency(item.unit_price)}</td>
                <td className="px-4 py-2.5 text-right text-xs font-body text-gray-800">{formatCurrency(item.total_price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* Totals */}
        <div className="border-t border-gray-100 px-4 py-3 space-y-1.5">
          <div className="flex justify-between text-xs font-body text-gray-500">
            <span>Subtotal</span><span>{formatCurrency(order.subtotal)}</span>
          </div>
          {order.discount_amount > 0 && (
            <div className="flex justify-between text-xs font-body text-green-600">
              <span>Discount</span><span>-{formatCurrency(order.discount_amount)}</span>
            </div>
          )}
          <div className="flex justify-between text-xs font-body text-gray-500">
            <span>Delivery</span>
            <span>{order.delivery_fee > 0 ? formatCurrency(order.delivery_fee) : 'Free'}</span>
          </div>
          <div className="flex justify-between text-sm font-body font-medium text-gray-800 pt-1 border-t border-gray-100">
            <span>Total</span><span>{formatCurrency(order.total)}</span>
          </div>
          <div className="flex justify-between text-xs font-body text-gray-400 mt-1">
            <span>VAT included (5%)</span><span>{formatCurrency(order.vat_amount)}</span>
          </div>
        </div>
        {economics && <NetPayment economics={economics} />}
      </div>

      {/* Admin Notes */}
      <div className="bg-white border border-gray-200 p-4">
        <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-2">Admin Notes</p>
        <textarea
          value={notes}
          onChange={e => setNotes(e.target.value)}
          rows={3}
          placeholder="Internal notes (not shown to customer)…"
          className="w-full px-3 py-2 text-xs font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary/30 resize-none"
        />
        <div className="flex justify-end mt-2">
          <Button size="sm" variant="ghost" onClick={saveNotes} loading={actionLoading}>
            Save Notes
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Fulfilment ────────────────────────────────────────────────────────────────

// Each courier's own vocabulary, stored verbatim and translated only here.
// Lalamove shouts, noon Send does not, so the two cannot collide.
const COURIER_STATUS_LABEL: Record<string, string> = {
  ASSIGNING_DRIVER: 'Finding a driver',
  ON_GOING: 'Driver on the way to us',
  PICKED_UP: 'Collected',
  COMPLETED: 'Delivered',
  CANCELED: 'Cancelled',
  REJECTED: 'Rejected by drivers',
  EXPIRED: 'Expired — nobody accepted',
  created: 'Task created',
  pending_assignment: 'Finding a rider',
  assigned: 'Rider on the way to us',
  arrived_at_pickup_location: 'Rider at the kitchen',
  picked_up: 'Collected',
  arrived_at_delivery: 'Rider at the door',
  delivered: 'Delivered',
  undelivered: 'Could not be handed over',
  cancelled: 'Cancelled',
};

const DELIVERED_STATUSES = new Set(['COMPLETED', 'delivered']);

/**
 * The one decision this whole flow exists for: is this delivery worth the fee.
 *
 * A bespoke dialog rather than the shared `useConfirm` every other action on
 * this page uses, because the answer depends on four numbers and a sentence
 * cannot carry them legibly. It stays local to this file — the shared confirm
 * takes a message, and stretching it to render arbitrary quote tables for a
 * single caller would be guessing at the second caller's requirements.
 */
function AssignLalamoveDialog({
  quote,
  expired,
  busy,
  onConfirm,
  onCancel,
}: {
  quote: LalamoveQuote;
  expired: boolean;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const currency = quote.currency || 'AED';
  const losesMoney = quote.margin !== null && quote.margin < 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="assign-lalamove-title"
      // Clicking away cancels; clicking the card must not. Nothing is booked
      // either way — the booking is the button.
      onClick={onCancel}
    >
      <div
        className="bg-white border border-gray-200 w-full max-w-sm p-5"
        onClick={e => e.stopPropagation()}
      >
        <h2
          id="assign-lalamove-title"
          className="font-display text-lg text-primary mb-1"
        >
          Assign to Lalamove
        </h2>
        <p className="text-xs text-gray-500 font-body mb-4">
          A courier is booked immediately. The customer is not charged anything
          more — this is what the delivery costs us.
        </p>

        {expired && (
          <p className="text-xs font-body text-amber-700 bg-amber-50 border border-amber-200 p-2 mb-3">
            The previous price expired. This is the current one — confirm again
            to book at it.
          </p>
        )}

        <dl className="text-sm font-body border-t border-gray-100">
          <div className="flex justify-between py-2 border-b border-gray-100">
            <dt className="text-gray-500">Lalamove quote</dt>
            <dd className="text-gray-900">
              {currency} {quote.cost.toFixed(2)}
              {quote.distance_m !== null && (
                <span className="text-gray-400">
                  {' '}
                  · {(quote.distance_m / 1000).toFixed(1)} km
                </span>
              )}
            </dd>
          </div>
          <div className="flex justify-between py-2 border-b border-gray-100">
            <dt className="text-gray-500">Customer paid</dt>
            <dd className="text-gray-900">
              {quote.fee_charged === null
                ? '—'
                : `${currency} ${quote.fee_charged.toFixed(2)}`}
            </dd>
          </div>
          <div className="flex justify-between py-2 border-b border-gray-100">
            <dt className="text-gray-500">Margin</dt>
            <dd className={cn(losesMoney ? 'text-red-600' : 'text-gray-900')}>
              {quote.margin === null
                ? '—'
                : `${quote.margin < 0 ? '\u2212' : ''}${currency} ${Math.abs(quote.margin).toFixed(2)}`}
            </dd>
          </div>
        </dl>

        {losesMoney && (
          <p className="text-xs font-body text-red-600 mt-3">
            This delivery loses money. Assign it anyway only if the order needs
            to go out today.
          </p>
        )}

        <div className="flex justify-end gap-2 mt-5">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button size="sm" onClick={onConfirm} disabled={busy}>
            {busy ? 'Booking…' : 'Confirm & book'}
          </Button>
        </div>
      </div>
    </div>
  );
}

const PROVIDER_LABEL: Record<string, string> = {
  lalamove: 'Lalamove',
  noon_send: 'noon Send',
  third_party: 'Third party',
};

/**
 * What it cost to get this order out of the door.
 *
 * The customer is never shown any of this. It is here for the two questions
 * the shop actually has: is somebody coming for this box, and did we make
 * money on the delivery.
 */
function DeliveryPanel({
  delivery,
  busy,
  onRedispatch,
  onRefresh,
  onAssignLalamove,
  canAssignLalamove,
  isSettled,
}: {
  delivery: OrderDelivery;
  busy: boolean;
  onRedispatch: () => void;
  onRefresh: () => void;
  onAssignLalamove: () => void;
  /** Packed and nothing else — see `_assert_assignable` on the API side. */
  canAssignLalamove: boolean;
  /**
   * The order is not going anywhere: cancelled, undelivered, refunded or
   * disputed. Every control that would call a driver or ask a courier for an
   * update disappears.
   *
   * `undelivered` counts. It is cancellation after the box was made, and it
   * used to offer Re-dispatch on the reasoning that the cake exists and is paid
   * for — which is how a written-off order gets a second van sent to it.
   *
   * The API refuses these too, and that is the enforcement. This only stops the
   * screen offering a button whose single possible outcome is a 409.
   */
  isSettled: boolean;
}) {
  const cost = delivery.cost_total ?? delivery.quoted_cost;
  const isCourier = delivery.provider !== 'third_party';
  // noon Send publishes a rate card and no quotation API, so their number is
  // computed here rather than billed. Saying so stops it being read as an
  // invoice line.
  const costIsEstimate = delivery.provider === 'noon_send';

  return (
    <div
      className={cn(
        'bg-white border mb-4',
        delivery.needs_attention ? 'border-red-300' : 'border-gray-200',
      )}
    >
      <div className="flex items-center gap-2 px-4 pt-4 pb-2">
        <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 flex-1">
          Fulfilment
        </p>
        <Badge
          variant={
            delivery.provider === 'noon_send'
              ? 'success'
              : isCourier
                ? 'info'
                : 'neutral'
          }
        >
          {PROVIDER_LABEL[delivery.provider] ?? delivery.provider}
        </Badge>
        {/* This order is not on the courier its zone chose. Worth saying: the
            provider badge alone makes a reassigned order look like one that was
            always Lalamove, and the two cost different things to explain. */}
        {delivery.original_provider &&
          delivery.original_provider !== delivery.provider && (
            <span className="text-[10px] font-body text-gray-400">
              moved from {PROVIDER_LABEL[delivery.original_provider] ?? delivery.original_provider}
            </span>
          )}
        {delivery.courier_status && (
          <Badge
            variant={
              DELIVERED_STATUSES.has(delivery.courier_status)
                ? 'success'
                : delivery.needs_attention
                  ? 'danger'
                  : 'info'
            }
          >
            {COURIER_STATUS_LABEL[delivery.courier_status] ?? delivery.courier_status}
          </Badge>
        )}
      </div>

      {delivery.last_error && (
        <p className="mx-4 mb-3 px-3 py-2 text-xs font-body text-red-700 bg-red-50 border border-red-200">
          {delivery.last_error}
        </p>
      )}

      {/* First in the list because it is what a driver or a courier's support
          desk will open a call with. */}
      {delivery.courier_reference && (
        <p className="mx-4 mb-3 px-3 py-2 text-xs font-body text-gray-600 bg-gray-50 border border-gray-200">
          Driver reference{' '}
          <span className="font-mono text-sm text-gray-900 tracking-wider">
            {delivery.courier_reference}
          </span>
        </p>
      )}

      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4 px-4 pb-4 text-xs font-body">
        <div>
          <dt className="text-gray-500">Zone</dt>
          <dd className="text-gray-800">{delivery.zone_name ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-gray-500">Charged</dt>
          <dd className="text-gray-800">
            {delivery.fee_charged !== null
              ? delivery.fee_charged > 0
                ? formatCurrency(delivery.fee_charged)
                : 'Free'
              : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">
            Courier cost{costIsEstimate && ' (est.)'}
          </dt>
          <dd className="text-gray-800">
            {cost !== null ? formatCurrency(cost) : '—'}
            {delivery.quoted_distance_m !== null && (
              <span className="text-gray-400">
                {' '}
                · {(delivery.quoted_distance_m / 1000).toFixed(1)} km
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">Margin</dt>
          <dd className={cn(delivery.margin !== null && delivery.margin < 0 ? 'text-red-600' : 'text-gray-800')}>
            {delivery.margin !== null ? formatCurrency(delivery.margin) : '—'}
          </dd>
        </div>

        {delivery.driver_name && (
          <div className="col-span-2">
            <dt className="text-gray-500">
              Driver
              {/* Said out loud, because a swapped booking used to render exactly
                  like an unswapped one — and the person on the phone to the
                  courier had no way to know they were describing somebody who
                  had dropped the job an hour ago. */}
              {delivery.driver_assignment_count > 1 && (
                <span className="ml-1 text-amber-700">
                  · reassigned ({ordinal(delivery.driver_assignment_count)} driver)
                </span>
              )}
            </dt>
            <dd className="text-gray-800">
              {delivery.driver_name}
              {delivery.driver_phone && ` · ${delivery.driver_phone}`}
              {delivery.driver_plate && ` · ${delivery.driver_plate}`}
            </dd>
            {delivery.driver_assigned_at && (
              <dd className="text-[11px] text-gray-400">
                since {formatDateTime(delivery.driver_assigned_at)}
              </dd>
            )}
          </div>
        )}
        {/* How far they still are from the kitchen — the one question somebody
            standing over a boxed order is actually asking. Absent rather than
            zero when the courier has gone quiet or the parcel is already on the
            bike; see `driver_proximity` for why silence beats a stale figure. */}
        {delivery.driver_distance_km !== null && (
          <div className="col-span-2">
            <dt className="text-gray-500">Distance from branch</dt>
            <dd className="text-gray-800">
              {/* The ETA leads where there is one — it is the number a person
                  actually plans against — and its presence is also what says
                  the figures were routed rather than estimated. The tilde on
                  the kilometres survives only on the fallback, where it is
                  earned. */}
              {delivery.driver_eta_minutes !== null ? (
                <>
                  <span className="font-medium">
                    {Math.round(delivery.driver_eta_minutes)} min
                  </span>
                  {' · '}
                  {delivery.driver_distance_km.toFixed(1)} km away
                </>
              ) : (
                <>~{delivery.driver_distance_km.toFixed(1)} km away</>
              )}
              {delivery.driver_location_at && (
                <span className="text-gray-400">
                  {' '}· as of {formatTimeAgo(delivery.driver_location_at)}
                </span>
              )}
            </dd>
            {delivery.driver_eta_minutes === null && (
              // Said rather than left to be inferred from a missing number: an
              // estimate and a routed answer look identical at a glance, and
              // only one of them accounts for the bridge.
              <dd className="text-[11px] text-gray-400">
                Straight-line estimate — no live route available
              </dd>
            )}
          </div>
        )}
        {delivery.previous_drivers.length > 0 && (
          <div className="col-span-2 sm:col-span-4">
            <dt className="text-gray-500">Previous drivers</dt>
            <dd className="text-gray-600 space-y-0.5">
              {delivery.previous_drivers.map((driver) => (
                <div key={driver.sequence} className="text-[11px]">
                  {driver.name ?? 'Unnamed'}
                  {driver.phone && ` · ${driver.phone}`}
                  {driver.replaced_at &&
                    ` · replaced ${formatDateTime(driver.replaced_at)}`}
                </div>
              ))}
            </dd>
          </div>
        )}
        {delivery.booked_at && (
          <div>
            <dt className="text-gray-500">Booked</dt>
            <dd className="text-gray-800">{formatDateTime(delivery.booked_at)}</dd>
          </div>
        )}
        {delivery.delivered_at && (
          <div>
            <dt className="text-gray-500">Delivered</dt>
            <dd className="text-gray-800">{formatDateTime(delivery.delivered_at)}</dd>
          </div>
        )}
      </dl>

      {(isCourier || delivery.pod_image_url) && (
        <div className="flex flex-wrap items-center gap-2 px-4 pb-4">
          {delivery.share_link && (
            <a
              href={delivery.share_link}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center min-h-11 md:min-h-0 text-xs font-body text-primary hover:underline"
            >
              Live tracking (internal)
            </a>
          )}
          {delivery.pod_image_url && (
            <a
              href={delivery.pod_image_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center min-h-11 md:min-h-0 text-xs font-body text-primary hover:underline"
            >
              Proof of delivery
            </a>
          )}
          <div className="flex-1" />
          {/* noon Send only. Lalamove pushes its own updates and retries them
              for a day, so the endpoint refuses it and a button here would be a
              400 waiting to happen. */}
          {!isSettled && delivery.provider === 'noon_send' && delivery.courier_order_id && (
            <Button size="sm" variant="ghost" onClick={onRefresh} disabled={busy}>
              <span className="material-icons text-[14px]">sync</span>
              Check status
            </Button>
          )}
          {/* A third-party zone has no integration — somebody we already use
              collects the box. This is the escape hatch for the day that is not
              good enough: quote Lalamove, look at the number, and decide. */}
          {!isSettled && !isCourier && canAssignLalamove && !delivery.courier_order_id && (
            <Button size="sm" variant="ghost" onClick={onAssignLalamove} disabled={busy}>
              <span className="material-icons text-[14px]">local_shipping</span>
              Assign to Lalamove
            </Button>
          )}
          {isCourier && !isSettled && (
            <Button size="sm" variant="ghost" onClick={onRedispatch} disabled={busy}>
              <span className="material-icons text-[14px]">refresh</span>
              {delivery.courier_order_id ? 'Re-dispatch' : 'Dispatch now'}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * What the shop actually kept, under the customer's totals.
 *
 * The block above says what was charged. This says what survived it — the van,
 * the processor, and anything sent back — because that is the number that
 * decides whether the order was worth taking, and until now working it out
 * meant opening a Stripe dashboard in another tab.
 *
 * **Two percentages, deliberately.** Against the total including fees, it says
 * how much of everything the customer handed over survives, which is the number
 * for deciding whether to run free delivery at all. Against the items alone, it
 * says what the cake earns once delivery is stripped out, which is the number
 * for pricing the cake. On a free-delivery order the two diverge sharply, and
 * showing only one invites the other to be guessed at.
 */
function NetPayment({ economics }: { economics: OrderEconomics }) {
  const negative = economics.net < 0;
  return (
    <div className="border-t border-gray-200 bg-gray-50 px-4 py-3 space-y-1.5">
      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">
        What we keep
      </p>
      {economics.courier_cost !== null ? (
        <div className="flex justify-between text-xs font-body text-gray-500">
          <span>Courier cost</span>
          <span>-{formatCurrency(economics.courier_cost)}</span>
        </div>
      ) : (
        /* A third-party zone bills nothing per order. Saying "not itemised"
           rather than showing zero, because a third party's van is not free —
           it is just not on this order's books. */
        <div className="flex justify-between text-xs font-body text-gray-400">
          <span>Courier cost</span><span>Not itemised</span>
        </div>
      )}
      <div className="flex justify-between text-xs font-body text-gray-500">
        <span>
          Payment processing
          {economics.processing_fee_is_estimated && (
            <span className="text-gray-400"> (est.)</span>
          )}
        </span>
        <span>-{formatCurrency(economics.processing_fee)}</span>
      </div>
      {economics.refunded > 0 && (
        <div className="flex justify-between text-xs font-body text-red-600">
          <span>Refunded</span><span>-{formatCurrency(economics.refunded)}</span>
        </div>
      )}
      <div
        className={cn(
          'flex justify-between text-sm font-body font-medium pt-1 border-t border-gray-200',
          negative ? 'text-red-600' : 'text-gray-800'
        )}
      >
        <span>Net</span><span>{formatCurrency(economics.net)}</span>
      </div>
      <div className="flex justify-between text-[11px] font-body text-gray-400">
        <span>of total charged</span>
        <span>{economics.margin_on_charged !== null ? `${economics.margin_on_charged.toFixed(1)}%` : '—'}</span>
      </div>
      <div className="flex justify-between text-[11px] font-body text-gray-400">
        <span>of items value</span>
        <span>{economics.margin_on_items !== null ? `${economics.margin_on_items.toFixed(1)}%` : '—'}</span>
      </div>
    </div>
  );
}
