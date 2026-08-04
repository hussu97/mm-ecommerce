'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { ApiError, trackApi } from '@/lib/api';
import type { OrderStatus, TrackResult } from '@/lib/types';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { analytics } from '@/lib/analytics';
import { FulfilmentPanel } from '@/components/order/FulfilmentPanel';

const STATUS_VARIANT: Record<OrderStatus, string> = {
  created: 'bg-yellow-50 text-yellow-700',
  confirmed: 'bg-blue-50 text-blue-700',
  packed: 'bg-purple-50 text-purple-700',
  out_for_delivery: 'bg-indigo-50 text-indigo-700',
  delivered: 'bg-green-50 text-green-700',
  cancelled: 'bg-red-50 text-red-700',
  payment_failed: 'bg-red-50 text-red-700',
  refunded: 'bg-gray-100 text-gray-600',
  disputed: 'bg-orange-50 text-orange-700',
};

function getDeeplinkForm() {
  if (typeof window === 'undefined') return { order_number: '', email: '' };
  const params = new URLSearchParams(window.location.search);
  return {
    order_number: params.get('order_number') ?? '',
    email: params.get('email') ?? '',
  };
}

export default function TrackPage() {
  const { t } = useTranslation();
  const deeplinkAttempted = useRef(false);

  const [form, setForm] = useState(getDeeplinkForm);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<TrackResult | null>(null);

  const STATUS_LABEL: Record<OrderStatus, string> = {
    created: t('order.status_pending'),
    confirmed: t('order.status_confirmed'),
    packed: t('order.status_packed'),
    out_for_delivery: t('order.status_out_for_delivery'),
    delivered: t('order.status_delivered'),
    cancelled: t('order.status_cancelled'),
    payment_failed: t('order.status_payment_failed'),
    refunded: t('order.status_refunded'),
    disputed: t('order.status_disputed'),
  };

  const lookupOrder = useCallback(async (orderNumber: string, email: string) => {
    const trimmedOrderNumber = orderNumber.trim();
    const trimmedEmail = email.trim();
    if (!trimmedOrderNumber || !trimmedEmail) {
      setError(t('track.validation_error'));
      return;
    }
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const data = await trackApi.lookup(trimmedOrderNumber, trimmedEmail);
      analytics.orderTracked({ order_number: data.order_number, status: data.status });
      setResult(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('track.generic_error'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    const deeplinkForm = getDeeplinkForm();
    if (
      deeplinkAttempted.current ||
      !deeplinkForm.order_number ||
      !deeplinkForm.email
    ) {
      return;
    }
    deeplinkAttempted.current = true;
    void lookupOrder(deeplinkForm.order_number, deeplinkForm.email);
  }, [lookupOrder]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    await lookupOrder(form.order_number, form.email);
  }

  return (
    <div className="min-h-[70vh] flex items-start justify-center px-4 py-16">
      {/* Widens once there is something to show. The form alone is a narrow
          two-field thing; the result carries a timeline, an estimate and a
          branch card, and squeezing those into the form's width was why the
          collection address wrapped onto five lines on a phone. */}
      <div className={`w-full ${result ? 'max-w-lg' : 'max-w-md'} transition-[max-width]`}>
        <div className="text-center mb-8">
          <h1 className="font-display text-3xl text-primary uppercase tracking-widest mb-2">
            {t('track.title')}
          </h1>
          <p className="text-sm text-gray-500 font-body">
            {t('track.subtitle')}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-600 text-sm px-4 py-3 rounded-sm">
              {error}
            </div>
          )}
          <Input
            label={t('track.order_number')}
            value={form.order_number}
            onChange={e => setForm(f => ({ ...f, order_number: e.target.value }))}
            placeholder="e.g. MM-20260306-0001"
            autoComplete="off"
          />
          <Input
            label={t('track.email_address')}
            type="email"
            value={form.email}
            onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
            placeholder={t('common.email_placeholder')}
            autoComplete="email"
          />
          <Button type="submit" fullWidth loading={loading} size="lg">
            {t('track.track_button')}
          </Button>
        </form>

        {result && (
          <div className="mt-8 space-y-4">
            <div className="bg-white border border-gray-200 p-6 space-y-4">
              <div className="text-center">
                <p className="text-xs font-body uppercase tracking-widest text-gray-400 mb-1">{t('track.order_label')}</p>
                <p className="font-display text-xl text-primary">{result.order_number}</p>
              </div>
              <div className="h-px bg-gray-100" />
              <dl className="space-y-3">
                <div className="flex justify-between items-center">
                  <dt className="text-xs font-body uppercase tracking-widest text-gray-400">{t('track.status')}</dt>
                  <dd>
                    <span className={`text-xs font-body font-medium px-2 py-1 rounded-sm ${STATUS_VARIANT[result.status] ?? 'bg-gray-100 text-gray-700'}`}>
                      {STATUS_LABEL[result.status] ?? result.status}
                    </span>
                  </dd>
                </div>
                <div className="flex justify-between items-center">
                  <dt className="text-xs font-body uppercase tracking-widest text-gray-400">{t('track.delivery')}</dt>
                  <dd className="text-sm font-body text-gray-700">
                    {result.delivery_method === 'pickup' ? t('order.store_pickup') : t('common.delivery')}
                  </dd>
                </div>
                <div className="flex justify-between items-center">
                  <dt className="text-xs font-body uppercase tracking-widest text-gray-400">{t('track.items')}</dt>
                  <dd className="text-sm font-body text-gray-700">{result.items_count}</dd>
                </div>
                <div className="flex justify-between items-center">
                  <dt className="text-xs font-body uppercase tracking-widest text-gray-400">{t('track.total')}</dt>
                  <dd className="text-sm font-body text-gray-700">AED {Number(result.total).toFixed(2)}</dd>
                </div>
                <div className="flex justify-between items-center">
                  <dt className="text-xs font-body uppercase tracking-widest text-gray-400">{t('track.placed')}</dt>
                  <dd className="text-sm font-body text-gray-700">{result.created_at}</dd>
                </div>
              </dl>
            </div>

            {/* The reason anybody opens this page twice. Same component, same
                data and therefore the same answer as the account page and the
                emails — a guest is not shown a worse version of the truth. */}
            <FulfilmentPanel fulfilment={result.fulfilment} />
          </div>
        )}
      </div>
    </div>
  );
}
