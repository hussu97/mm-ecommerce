'use client';

import Link from 'next/link';
import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { ordersApi } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import type { Order } from '@/lib/types';

function ConfirmationContent() {
  const searchParams = useSearchParams();
  const orderNumber = searchParams.get('order_number');

  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!orderNumber) { setLoading(false); setError(true); return; }
    ordersApi.get(orderNumber)
      .then(setOrder)
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, [orderNumber]);

  if (loading) {
    return (
      <div className="flex flex-col items-center gap-4 py-20">
        <Spinner size="lg" />
        <p className="font-body text-sm text-gray-400">Loading your order…</p>
      </div>
    );
  }

  if (error || !order) {
    return (
      <div className="flex flex-col items-center gap-6 py-20 text-center">
        <span className="material-icons text-5xl text-secondary">receipt_long</span>
        <h1 className="font-display text-2xl text-primary uppercase tracking-widest">Order not found</h1>
        <p className="font-body text-sm text-gray-500 max-w-sm">
          We couldn&apos;t retrieve your order details. If you completed payment, you&apos;ll receive a confirmation email shortly.
        </p>
        <Link href="/"><Button variant="primary">Back to Home</Button></Link>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-12">
      {/* Success header */}
      <div className="text-center mb-10">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-green-100 mb-4">
          <span className="material-icons text-3xl text-green-600">check_circle</span>
        </div>
        <h1 className="font-display text-3xl text-primary uppercase tracking-widest mb-2">
          Order Placed!
        </h1>
        <p className="font-body text-sm text-gray-500">
          Thank you for your order. We&apos;ll send a confirmation to <strong>{order.email}</strong>.
        </p>
      </div>

      {/* Order number */}
      <div className="bg-primary/5 border border-primary/20 rounded-sm px-6 py-4 text-center mb-6">
        <p className="font-body text-xs uppercase tracking-widest text-gray-500 mb-1">Order Number</p>
        <p className="font-display text-2xl text-primary">{order.order_number}</p>
      </div>

      {/* Order items */}
      <div className="border border-gray-100 rounded-sm divide-y divide-gray-100 mb-6">
        {order.items.map((item) => (
          <div key={item.id} className="flex justify-between items-start px-4 py-3">
            <div>
              <p className="font-body text-sm font-medium text-gray-800">{item.product_name}</p>
              <p className="font-body text-xs text-gray-400">{item.variant_name} × {item.quantity}</p>
            </div>
            <p className="font-body text-sm text-gray-700">{Number(item.total_price).toFixed(2)} AED</p>
          </div>
        ))}
      </div>

      {/* Totals */}
      <div className="space-y-2 font-body text-sm mb-6 px-1">
        <div className="flex justify-between">
          <span className="text-gray-500">Subtotal</span>
          <span>{Number(order.subtotal).toFixed(2)} AED</span>
        </div>
        {Number(order.discount_amount) > 0 && (
          <div className="flex justify-between text-green-700">
            <span>Discount{order.promo_code_used ? ` (${order.promo_code_used})` : ''}</span>
            <span>-{Number(order.discount_amount).toFixed(2)} AED</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-gray-500">
            {order.delivery_method === 'pickup' ? 'Pickup' : 'Delivery'}
          </span>
          <span className={Number(order.delivery_fee) === 0 ? 'text-green-600' : ''}>
            {Number(order.delivery_fee) === 0 ? 'Free' : `${Number(order.delivery_fee).toFixed(2)} AED`}
          </span>
        </div>
        <div className="h-px bg-gray-200" />
        <div className="flex justify-between font-semibold text-base">
          <span>Total Paid</span>
          <span className="text-primary">{Number(order.total).toFixed(2)} AED</span>
        </div>
      </div>

      {/* Delivery info */}
      {order.delivery_method === 'delivery' && order.shipping_address_snapshot && (
        <div className="bg-gray-50 rounded-sm p-4 mb-6">
          <p className="font-body text-xs uppercase tracking-widest text-gray-500 mb-2">Delivering to</p>
          <p className="font-body text-sm text-gray-800">
            {order.shipping_address_snapshot.address_line_1}
            {order.shipping_address_snapshot.address_line_2
              ? `, ${order.shipping_address_snapshot.address_line_2}`
              : ''}
          </p>
          <p className="font-body text-sm text-gray-600">
            {order.shipping_address_snapshot.city}, {order.shipping_address_snapshot.emirate}
          </p>
        </div>
      )}
      {order.delivery_method === 'pickup' && (
        <div className="bg-gray-50 rounded-sm p-4 mb-6 flex gap-2 items-start">
          <span className="material-icons text-base text-primary mt-0.5">storefront</span>
          <p className="font-body text-sm text-gray-600">
            You selected store pickup. We&apos;ll contact you via WhatsApp when your order is ready to collect.
          </p>
        </div>
      )}

      {/* CTAs */}
      <div className="flex flex-col sm:flex-row gap-3">
        <Link href="/" className="flex-1">
          <Button variant="ghost" size="lg" fullWidth>
            Continue Shopping
          </Button>
        </Link>
        <Link href={`/account/orders`} className="flex-1">
          <Button variant="primary" size="lg" fullWidth>
            View My Orders
          </Button>
        </Link>
      </div>
    </div>
  );
}

export default function ConfirmationPage() {
  return (
    <Suspense fallback={
      <div className="flex justify-center items-center py-20">
        <Spinner size="lg" />
      </div>
    }>
      <ConfirmationContent />
    </Suspense>
  );
}
