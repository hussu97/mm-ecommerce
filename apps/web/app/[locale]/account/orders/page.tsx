'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ordersApi } from '@/lib/api';
import { Order, OrderStatus } from '@/lib/types';

const STATUS_CONFIG: Record<OrderStatus, { label: string; classes: string }> = {
  created:   { label: 'Pending',   classes: 'bg-yellow-50 text-yellow-700 border-yellow-200' },
  confirmed: { label: 'Confirmed', classes: 'bg-blue-50 text-blue-700 border-blue-200' },
  packed:    { label: 'Packed',    classes: 'bg-purple-50 text-purple-700 border-purple-200' },
  cancelled: { label: 'Cancelled', classes: 'bg-red-50 text-red-700 border-red-200' },
};

export default function OrdersPage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    ordersApi.list()
      .then(res => setOrders(res.items))
      .catch(() => setError('Failed to load orders.'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div>
        <h1 className="font-display text-2xl text-primary mb-6">My Orders</h1>
        <div className="space-y-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="h-20 bg-gray-100 animate-pulse rounded-sm" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1 className="font-display text-2xl text-primary mb-6">My Orders</h1>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 text-sm px-4 py-3 rounded-sm mb-4">
          {error}
        </div>
      )}

      {orders.length === 0 && !error ? (
        <div className="text-center py-16 border border-dashed border-gray-200">
          <span className="material-icons text-4xl text-gray-300 block mb-3">receipt_long</span>
          <p className="text-sm text-gray-500 font-body mb-4">You haven&apos;t placed any orders yet.</p>
          <Link href="/" className="text-sm text-primary hover:underline font-body">
            Start shopping
          </Link>
        </div>
      ) : (
        <div className="space-y-3">
          {orders.map(order => {
            const status = STATUS_CONFIG[order.status];
            return (
              <Link
                key={order.id}
                href={`/account/orders/${order.order_number}`}
                className="flex items-center justify-between p-4 border border-gray-200 hover:border-primary transition-colors group"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-3 mb-1">
                    <span className="text-sm font-medium text-gray-800 font-body">
                      {order.order_number}
                    </span>
                    <span className={`text-[11px] font-body uppercase tracking-wide px-2 py-0.5 border ${status.classes}`}>
                      {status.label}
                    </span>
                  </div>
                  <p className="text-xs text-gray-400 font-body">
                    {new Date(order.created_at).toLocaleDateString('en-AE', {
                      day: 'numeric', month: 'short', year: 'numeric',
                    })}
                    {' · '}
                    {order.items?.length ?? 0} item{(order.items?.length ?? 0) !== 1 ? 's' : ''}
                  </p>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <span className="text-sm font-medium text-gray-800 font-body">
                    AED {Number(order.total).toFixed(2)}
                  </span>
                  <span className="material-icons text-gray-300 group-hover:text-primary transition-colors text-[18px]">
                    chevron_right
                  </span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
