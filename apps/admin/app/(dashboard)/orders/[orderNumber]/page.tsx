'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { ordersApi, ApiError } from '@/lib/api';
import type { Order, OrderDelivery, OrderStatus } from '@/lib/types';
import { Badge, Button } from '@/components/ui';
import { cn, formatCurrency, formatDate } from '@/lib/utils';

const STATUS_STEPS: OrderStatus[] = [
  'created',
  'confirmed',
  'packed',
  'out_for_delivery',
  'delivered',
];

const STATUS_LABEL: Record<OrderStatus, string> = {
  created: 'created',
  confirmed: 'confirmed',
  packed: 'packed',
  out_for_delivery: 'on the way',
  delivered: 'delivered',
  cancelled: 'cancelled',
};

const STATUS_VARIANT: Record<OrderStatus, 'warning' | 'info' | 'success' | 'danger'> = {
  created: 'warning',
  confirmed: 'info',
  packed: 'info',
  out_for_delivery: 'info',
  delivered: 'success',
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
  const currentStepIdx = STATUS_STEPS.indexOf(order.status as OrderStatus);

  return (
    <div className="max-w-3xl">
      {/* Back + header */}
      <div className="flex items-center gap-3 mb-6">
        <Link href="/orders" className="text-gray-400 hover:text-primary transition-colors">
          <span className="material-icons text-[20px]">arrow_back</span>
        </Link>
        <div className="flex-1">
          <h1 className="font-display text-xl text-gray-800">{order.order_number}</h1>
          <p className="text-xs text-gray-400 font-body">{formatDate(order.created_at)}</p>
        </div>
        <Badge variant={STATUS_VARIANT[order.status]}>{order.status}</Badge>
      </div>

      {/* Status timeline */}
      {!isCancelled && (
        <div className="bg-white border border-gray-200 p-4 mb-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-3">Status</p>
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
        {(order.status === 'packed' || order.status === 'out_for_delivery') && (
          <Button size="sm" onClick={() => updateStatus('delivered')} loading={actionLoading}>
            <span className="material-icons text-[14px]">done_all</span>
            Mark Delivered
          </Button>
        )}
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
          {order.shipping_address_snapshot && (
            <div className="mt-2 text-xs font-body text-gray-500 space-y-0.5">
              <p>{order.shipping_address_snapshot.first_name} {order.shipping_address_snapshot.last_name}</p>
              <p>{order.shipping_address_snapshot.phone}</p>
              <p>{order.shipping_address_snapshot.address_line_1}</p>
              {order.shipping_address_snapshot.address_line_2 && <p>{order.shipping_address_snapshot.address_line_2}</p>}
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
            <div className="flex justify-between">
              <dt className="text-gray-500">Payment</dt>
              <dd className="text-gray-700 capitalize">{order.payment_provider ?? '—'}</dd>
            </div>
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
}: {
  delivery: OrderDelivery;
  busy: boolean;
  onRedispatch: () => void;
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
            <dd className="text-gray-800">{formatDate(delivery.booked_at)}</dd>
          </div>
        )}
        {delivery.delivered_at && (
          <div>
            <dt className="text-gray-500">Delivered</dt>
            <dd className="text-gray-800">{formatDate(delivery.delivered_at)}</dd>
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
