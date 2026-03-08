'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { use } from 'react';
import { ordersApi } from '@/lib/api';
import { Order, OrderStatus } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useTranslation } from '@/lib/i18n/TranslationProvider';

const STATUS_ORDER: OrderStatus[] = ['created', 'confirmed', 'packed'];

export default function OrderDetailPage({ params }: { params: Promise<{ orderNumber: string }> }) {
  const { orderNumber } = use(params);
  const { t } = useTranslation();
  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const STATUS_CONFIG: Record<OrderStatus, { label: string; classes: string; icon: string }> = {
    created:   { label: t('order.timeline_placed'),    classes: 'text-yellow-600',  icon: 'pending' },
    confirmed: { label: t('order.status_confirmed'),   classes: 'text-blue-600',    icon: 'check_circle' },
    packed:    { label: t('order.status_packed'),      classes: 'text-purple-600',  icon: 'inventory_2' },
    cancelled: { label: t('order.status_cancelled'),   classes: 'text-red-500',     icon: 'cancel' },
  };

  const TIMELINE_STEPS: { status: OrderStatus; label: string; icon: string }[] = [
    { status: 'created',   label: t('order.timeline_placed'),    icon: 'receipt' },
    { status: 'confirmed', label: t('order.timeline_confirmed'), icon: 'check_circle' },
    { status: 'packed',    label: t('order.timeline_ready'),     icon: 'inventory_2' },
  ];

  useEffect(() => {
    ordersApi.get(orderNumber)
      .then(setOrder)
      .catch(() => setError(t('order.not_found')))
      .finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderNumber]);

  if (loading) {
    return (
      <div>
        <div className="h-7 w-48 bg-gray-100 animate-pulse mb-6 rounded-sm" />
        <div className="space-y-4">
          {[1, 2, 3].map(i => <div key={i} className="h-24 bg-gray-100 animate-pulse rounded-sm" />)}
        </div>
      </div>
    );
  }

  if (error || !order) {
    return (
      <div className="text-center py-16">
        <span className="material-icons text-4xl text-gray-300 block mb-3">search_off</span>
        <p className="text-sm text-gray-500 font-body mb-4">{error || t('order.not_found')}</p>
        <Link href="/account/orders" className="text-sm text-primary hover:underline font-body">
          {t('order.back_to_orders')}
        </Link>
      </div>
    );
  }

  const isCancelled = order.status === 'cancelled';
  const currentIndex = STATUS_ORDER.indexOf(order.status);
  const statusInfo = STATUS_CONFIG[order.status];
  const addr = order.shipping_address_snapshot as Record<string, string> | null;

  return (
    <div>
      <div className="flex items-center gap-3 mb-6">
        <Link href="/account/orders" className="text-gray-400 hover:text-primary transition-colors">
          <span className="material-icons text-[20px]">arrow_back</span>
        </Link>
        <div>
          <h1 className="font-display text-2xl text-primary leading-tight">{order.order_number}</h1>
          <p className="text-xs text-gray-400 font-body">
            {t('order.placed')} {new Date(order.created_at).toLocaleDateString('en-AE', { day: 'numeric', month: 'long', year: 'numeric' })}
          </p>
        </div>
        <span className={`ml-auto text-[11px] font-body uppercase tracking-wide px-2.5 py-1 border ${
          isCancelled ? 'bg-red-50 text-red-700 border-red-200' :
          order.status === 'packed' ? 'bg-purple-50 text-purple-700 border-purple-200' :
          order.status === 'confirmed' ? 'bg-blue-50 text-blue-700 border-blue-200' :
          'bg-yellow-50 text-yellow-700 border-yellow-200'
        }`}>
          {statusInfo.label}
        </span>
      </div>

      {/* Status Timeline */}
      {!isCancelled && (
        <div className="mb-8 p-5 border border-gray-100 bg-gray-50">
          <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-5">{t('order.order_progress')}</h2>
          <div className="flex items-center">
            {TIMELINE_STEPS.map((step, idx) => {
              const done = currentIndex >= idx;
              const active = currentIndex === idx;
              return (
                <div key={step.status} className="flex items-center flex-1 last:flex-none">
                  <div className="flex flex-col items-center">
                    <span className={cn(
                      'material-icons text-[22px] transition-colors',
                      done ? (active ? 'text-primary' : 'text-green-500') : 'text-gray-300',
                    )}>
                      {done && !active ? 'check_circle' : step.icon}
                    </span>
                    <p className={cn(
                      'text-[10px] font-body mt-1.5 text-center leading-tight max-w-[70px] uppercase tracking-wider',
                      done ? 'text-gray-700' : 'text-gray-300',
                    )}>
                      {step.label}
                    </p>
                  </div>
                  {idx < TIMELINE_STEPS.length - 1 && (
                    <div className={cn(
                      'flex-1 h-px mx-2 mb-5',
                      currentIndex > idx ? 'bg-green-400' : 'bg-gray-200',
                    )} />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {isCancelled && order.admin_notes && (
        <div className="mb-6 bg-red-50 border border-red-200 p-4">
          <p className="text-xs font-medium text-red-700 uppercase tracking-widest mb-1">{t('order.cancellation_note')}</p>
          <p className="text-sm text-red-600 font-body">{order.admin_notes}</p>
        </div>
      )}

      {/* Order Items */}
      <div className="mb-6">
        <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-3">{t('order.items')}</h2>
        <div className="border border-gray-200 divide-y divide-gray-100">
          {order.items.map(item => (
            <div key={item.id} className="flex items-center justify-between px-4 py-3">
              <div>
                <p className="text-sm font-body text-gray-800">{item.product_name}</p>
                {item.selected_options_snapshot && item.selected_options_snapshot.length > 0 && (
                  <p className="text-xs text-gray-400 font-body">
                    {item.selected_options_snapshot.map(o => o.option_name).join(', ')}
                  </p>
                )}
              </div>
              <div className="text-right">
                <p className="text-sm font-body text-gray-800">AED {Number(item.total_price).toFixed(2)}</p>
                <p className="text-xs text-gray-400 font-body">
                  {item.quantity} × AED {Number(item.unit_price).toFixed(2)}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Totals */}
      <div className="mb-6 border border-gray-200 p-4 space-y-2">
        <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-3">{t('order.summary')}</h2>
        <div className="flex justify-between text-sm font-body text-gray-600">
          <span>{t('common.subtotal')}</span>
          <span>AED {Number(order.subtotal).toFixed(2)}</span>
        </div>
        {Number(order.discount_amount) > 0 && (
          <div className="flex justify-between text-sm font-body text-green-600">
            <span>{t('common.discount')} {order.promo_code_used ? `(${order.promo_code_used})` : ''}</span>
            <span>− AED {Number(order.discount_amount).toFixed(2)}</span>
          </div>
        )}
        <div className="flex justify-between text-sm font-body text-gray-600">
          <span>{t('common.delivery')}</span>
          <span>{Number(order.delivery_fee) === 0 ? t('common.free') : `AED ${Number(order.delivery_fee).toFixed(2)}`}</span>
        </div>
        <div className="flex justify-between text-sm font-semibold font-body text-gray-900 pt-2 border-t border-gray-100">
          <span>{t('common.total')}</span>
          <span>AED {Number(order.total).toFixed(2)}</span>
        </div>
      </div>

      {/* Delivery Info */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="border border-gray-200 p-4">
          <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-2">{t('common.delivery')}</h2>
          {order.delivery_method === 'pickup' ? (
            <p className="text-sm text-gray-700 font-body">{t('order.store_pickup')}</p>
          ) : addr ? (
            <div className="text-sm text-gray-700 font-body space-y-0.5">
              <p>{addr.first_name} {addr.last_name}</p>
              <p>{addr.address_line_1}</p>
              {addr.address_line_2 && <p>{addr.address_line_2}</p>}
              <p>{addr.city}, {addr.emirate}</p>
              {addr.phone && <p className="text-gray-400">{addr.phone}</p>}
            </div>
          ) : (
            <p className="text-sm text-gray-400 font-body">{t('order.no_address')}</p>
          )}
        </div>

        <div className="border border-gray-200 p-4">
          <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-2">{t('order.payment')}</h2>
          <p className="text-sm text-gray-700 font-body capitalize">
            {order.payment_provider || order.payment_method || '—'}
          </p>
          {order.notes && (
            <div className="mt-3">
              <p className="text-xs text-gray-400 uppercase tracking-widest font-body mb-1">{t('order.notes')}</p>
              <p className="text-sm text-gray-600 font-body">{order.notes}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
