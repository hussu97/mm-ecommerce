'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { use } from 'react';
import { ordersApi } from '@/lib/api';
import { Order, OrderStatus } from '@/lib/types';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { FulfilmentPanel } from '@/components/order/FulfilmentPanel';

/**
 * How a status looks in the header chip.
 *
 * The order timeline used to live here as a hardcoded three-step list ending at
 * "packed", which is where the lifecycle stopped when it was written. It is now
 * `FulfilmentPanel`, driven by what the server says about this particular order
 * — so a collection order shows the collection journey, and a delivery with a
 * rider on it shows the rider.
 */
const STATUS_STYLE: Record<OrderStatus, string> = {
  created: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  confirmed: 'bg-blue-50 text-blue-700 border-blue-200',
  packed: 'bg-purple-50 text-purple-700 border-purple-200',
  out_for_delivery: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  delivered: 'bg-green-50 text-green-700 border-green-200',
  cancelled: 'bg-red-50 text-red-700 border-red-200',
  payment_failed: 'bg-red-50 text-red-700 border-red-200',
  refunded: 'bg-gray-50 text-gray-600 border-gray-200',
  disputed: 'bg-orange-50 text-orange-700 border-orange-200',
};

/** Statuses where nothing more is coming, so the progress panel is noise. */
const SETTLED: OrderStatus[] = ['cancelled', 'payment_failed', 'refunded', 'disputed'];

export default function OrderDetailPage({ params }: { params: Promise<{ orderNumber: string }> }) {
  const { orderNumber } = use(params);
  const { t, locale } = useTranslation();
  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const STATUS_LABEL: Record<OrderStatus, string> = {
    created: t('order.timeline_placed'),
    confirmed: t('order.status_confirmed'),
    packed: t('order.status_packed'),
    out_for_delivery: t('order.status_out_for_delivery'),
    delivered: t('order.status_delivered'),
    cancelled: t('order.status_cancelled'),
    payment_failed: t('order.status_payment_failed'),
    refunded: t('order.status_refunded'),
    disputed: t('order.status_disputed'),
  };

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

  const isSettled = SETTLED.includes(order.status);
  const isPickup = order.delivery_method === 'pickup';
  const addr = order.shipping_address_snapshot as Record<string, string> | null;
  // Collection orders carry their branch on the fulfilment block, and
  // `FulfilmentPanel` renders it. Rendering it here as well would put the same
  // address on the page twice.
  const branch = order.fulfilment?.branch ?? null;

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
        <span className={`ml-auto text-[11px] font-body uppercase tracking-wide px-2.5 py-1 border ${STATUS_STYLE[order.status]}`}>
          {STATUS_LABEL[order.status]}
        </span>
      </div>

      {/* When it arrives, where to collect it, whether there is a rider to
          watch — the whole of what the server is willing to say about it. */}
      <FulfilmentPanel fulfilment={order.fulfilment} className="mb-6" />

      {isSettled && order.admin_notes && (
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
                <p className="text-sm font-body text-gray-800">{localizedField({ translations: item.product_translations }, 'name', item.product_name, locale)}</p>
                {item.selected_options_snapshot && item.selected_options_snapshot.length > 0 && (
                  <p className="text-xs text-gray-400 font-body">
                    {item.selected_options_snapshot.map(o => localizedField({ translations: o.option_translations }, 'name', o.option_name, locale)).join(', ')}
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
          <span>{isPickup ? t('order.store_pickup') : t('common.delivery')}</span>
          <span>{Number(order.delivery_fee) === 0 ? t('common.free') : `AED ${Number(order.delivery_fee).toFixed(2)}`}</span>
        </div>
        <div className="flex justify-between text-sm font-semibold font-body text-gray-900 pt-2 border-t border-gray-100">
          <span>{t('common.total')}</span>
          <span>AED {Number(order.total).toFixed(2)}</span>
        </div>
        <div className="flex justify-between text-xs font-body text-gray-400 mt-1">
          <span>VAT included (5%)</span>
          <span>AED {Number(order.vat_amount).toFixed(2)}</span>
        </div>
      </div>

      {/* Where it is going, and how it was paid for. A collection order's
          branch is already above, on the fulfilment panel. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="border border-gray-200 p-4">
          <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-2">
            {isPickup ? t('order.store_pickup') : t('order.delivery_address')}
          </h2>
          {isPickup ? (
            <p className="text-sm text-gray-700 font-body">
              {branch
                ? ((locale === 'ar' && branch.name_ar) || branch.name)
                : t('order.store_pickup')}
            </p>
          ) : addr ? (
            <div className="text-sm text-gray-700 font-body space-y-0.5">
              <p>{addr.first_name} {addr.last_name}</p>
              <p>{addr.address_line_1}</p>
              {addr.address_line_2 && <p>{addr.address_line_2}</p>}
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
