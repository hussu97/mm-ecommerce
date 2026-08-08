'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { ordersApi, ApiError } from '@/lib/api';
import type { Order, OrderDelivery, OrderStatus } from '@/lib/types';
import { Badge, Button } from '@/components/ui';
import { cn, formatCurrency, formatDate, formatDateTime } from '@/lib/utils';

const STATUS_STEPS: OrderStatus[] = [
  'created',
  'confirmed',
  'packed',
  'out_for_delivery',
  'delivered',
];

/**
 * Where each step's timestamp comes from.
 *
 * Only four of the five have a real one. `confirmed` has no stamp of its own —
 * nothing records the moment payment cleared separately from the order being
 * written — and inventing one from `updated_at` would be a number that moves
 * every time anybody edits the order. Blank is the honest answer.
 */
const STEP_STAMP: Partial<Record<OrderStatus, (o: Order) => string>> = {
  created: o => formatDateTime(o.created_at),
  packed: o => (o.fulfilment?.packed_at ? formatDateTime(o.fulfilment.packed_at) : ''),
  out_for_delivery: o =>
    o.fulfilment?.picked_up_at ? formatDateTime(o.fulfilment.picked_up_at) : '',
  delivered: o =>
    o.fulfilment?.delivered_at ? formatDateTime(o.fulfilment.delivered_at) : '',
};

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

const STATUS_LABEL: Record<OrderStatus, string> = {
  created: 'created',
  confirmed: 'confirmed',
  packed: 'packed',
  out_for_delivery: 'on the way',
  delivered: 'delivered',
  undelivered: 'undelivered',
  cancelled: 'cancelled',
};

const STATUS_VARIANT: Record<OrderStatus, 'warning' | 'info' | 'success' | 'danger'> = {
  created: 'warning',
  confirmed: 'info',
  packed: 'info',
  out_for_delivery: 'info',
  delivered: 'success',
  undelivered: 'danger',
  cancelled: 'danger',
};

export default function OrderDetailPage() {
  const { orderNumber } = useParams<{ orderNumber: string }>();
  const [order, setOrder] = useState<Order | null>(null);
  const [delivery, setDelivery] = useState<OrderDelivery | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');

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

  async function updateStatus(newStatus: OrderStatus) {
    if (!order) return;
    // Cancelling goes through on the first click — it is the one status the
    // counter changes while the customer is still on the phone. Every other
    // move still asks.
    if (newStatus !== 'cancelled' && !confirm(`Set order to "${STATUS_LABEL[newStatus]}"?`)) return;
    setActionLoading(true);
    try {
      const updated = await ordersApi.updateStatus(orderNumber, newStatus, notes || undefined);
      setOrder(updated);
      loadDelivery();
    } catch (err) {
      alert((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function redispatch() {
    if (!confirm('Book the courier again for this order?')) return;
    setActionLoading(true);
    try {
      setDelivery(await ordersApi.dispatchDelivery(orderNumber));
    } catch (err) {
      alert((err as Error).message);
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
      alert((err as Error).message);
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
    } catch (err) {
      alert((err as Error).message);
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
      {/* Back + header */}
      <div className="flex items-center gap-3 mb-6">
        <Link href="/orders" className="text-gray-400 hover:text-primary transition-colors">
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
                      {STEP_STAMP[step]?.(order) ?? ''}
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
        {(order.status === 'packed' || order.status === 'out_for_delivery' || isUndelivered) && (
          <Button size="sm" onClick={() => updateStatus('delivered')} loading={actionLoading}>
            <span className="material-icons text-[14px]">done_all</span>
            Mark Delivered
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
        {/* Back to the shelf, which is where a second attempt starts from. */}
        {isUndelivered && (
          <Button size="sm" onClick={() => updateStatus('packed')} loading={actionLoading}>
            <span className="material-icons text-[14px]">restart_alt</span>
            Return To Packed
          </Button>
        )}
        {(order.status === 'created' || order.status === 'confirmed' || isUndelivered) && (
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
          {order.shipping_address_snapshot && (
            <div className="mt-2 text-xs font-body text-gray-500 space-y-0.5">
              <p>{order.shipping_address_snapshot.first_name} {order.shipping_address_snapshot.last_name}</p>
              <p>{order.shipping_address_snapshot.phone}</p>
              <p>{order.shipping_address_snapshot.address_line_1}</p>
              {order.shipping_address_snapshot.address_line_2 && <p>{order.shipping_address_snapshot.address_line_2}</p>}
              {/* The pin, not the typed address. A UAE address line is often
                  unsearchable — "villa 12, behind the mosque" is a real one —
                  and the coordinates are what the courier is sent to. */}
              {mapsHref && (
                <a
                  href={mapsHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-flex items-center gap-1.5 border border-gray-300 px-2.5 py-1.5 text-[11px] font-body text-gray-700 hover:border-primary hover:text-primary transition-colors"
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
                  {item.selected_options_snapshot && item.selected_options_snapshot.length > 0 && (
                    <div className="text-[11px] font-body text-gray-400 mt-0.5">
                      {item.selected_options_snapshot.map((o, i) => (
                        <span key={i}>{i > 0 ? ', ' : ''}{o.option_name}{o.option_price > 0 ? ` (+${o.option_price.toFixed(2)})` : ''}</span>
                      ))}
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
}: {
  delivery: OrderDelivery;
  busy: boolean;
  onRedispatch: () => void;
  onRefresh: () => void;
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
            <dt className="text-gray-500">Driver</dt>
            <dd className="text-gray-800">
              {delivery.driver_name}
              {delivery.driver_phone && ` · ${delivery.driver_phone}`}
              {delivery.driver_plate && ` · ${delivery.driver_plate}`}
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
              className="text-xs font-body text-primary hover:underline"
            >
              Live tracking (internal)
            </a>
          )}
          {delivery.pod_image_url && (
            <a
              href={delivery.pod_image_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs font-body text-primary hover:underline"
            >
              Proof of delivery
            </a>
          )}
          <div className="flex-1" />
          {/* noon Send only. Lalamove pushes its own updates and retries them
              for a day, so the endpoint refuses it and a button here would be a
              400 waiting to happen. */}
          {delivery.provider === 'noon_send' && delivery.courier_order_id && (
            <Button size="sm" variant="ghost" onClick={onRefresh} disabled={busy}>
              <span className="material-icons text-[14px]">sync</span>
              Check status
            </Button>
          )}
          {isCourier && (
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
