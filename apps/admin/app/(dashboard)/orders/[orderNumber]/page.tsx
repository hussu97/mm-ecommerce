'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { ordersApi } from '@/lib/api';
import type { Order, OrderStatus } from '@/lib/types';
import { Badge, Button } from '@/components/ui';
import { cn, formatCurrency, formatDate } from '@/lib/utils';

const STATUS_STEPS: OrderStatus[] = ['created', 'confirmed', 'packed'];

const STATUS_VARIANT: Record<OrderStatus, 'warning' | 'info' | 'success' | 'danger'> = {
  created: 'warning',
  confirmed: 'info',
  packed: 'success',
  cancelled: 'danger',
};

export default function OrderDetailPage() {
  const { orderNumber } = useParams<{ orderNumber: string }>();
  const [order, setOrder] = useState<Order | null>(null);
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

  async function updateStatus(newStatus: OrderStatus) {
    if (!order) return;
    if (!confirm(`Set order to "${newStatus}"?`)) return;
    setActionLoading(true);
    try {
      const updated = await ordersApi.updateStatus(orderNumber, newStatus, notes || undefined);
      setOrder(updated);
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
                      {step}
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
              <p>{order.shipping_address_snapshot.region}</p>
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
