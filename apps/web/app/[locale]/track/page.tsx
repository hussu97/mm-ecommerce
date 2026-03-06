'use client';

import { useState } from 'react';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { ApiError, trackApi } from '@/lib/api';

const STATUS_VARIANT: Record<string, string> = {
  created: 'bg-gray-100 text-gray-700',
  confirmed: 'bg-blue-100 text-blue-700',
  packed: 'bg-green-100 text-green-700',
  cancelled: 'bg-red-100 text-red-700',
};

interface TrackResult {
  order_number: string;
  status: string;
  delivery_method: string;
  items_count: number;
  created_at: string;
}

export default function TrackPage() {
  const [form, setForm] = useState({ order_number: '', email: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<TrackResult | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.order_number || !form.email) {
      setError('Please enter your order number and email.');
      return;
    }
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const data = await trackApi.lookup(form.order_number.trim(), form.email.trim());
      setResult(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="font-display text-3xl text-primary uppercase tracking-widest mb-2">
            Track Your Order
          </h1>
          <p className="text-sm text-gray-500 font-body">
            Enter your order number and email to check the status.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-600 text-sm px-4 py-3 rounded-sm">
              {error}
            </div>
          )}
          <Input
            label="Order Number"
            value={form.order_number}
            onChange={e => setForm(f => ({ ...f, order_number: e.target.value }))}
            placeholder="e.g. MM-20260306-0001"
            autoComplete="off"
          />
          <Input
            label="Email Address"
            type="email"
            value={form.email}
            onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
            placeholder="you@example.com"
            autoComplete="email"
          />
          <Button type="submit" fullWidth loading={loading} size="lg">
            Track Order
          </Button>
        </form>

        {result && (
          <div className="mt-8 bg-white border border-gray-200 p-6 space-y-4">
            <div className="text-center">
              <p className="text-xs font-body uppercase tracking-widest text-gray-400 mb-1">Order</p>
              <p className="font-display text-xl text-primary">{result.order_number}</p>
            </div>
            <div className="h-px bg-gray-100" />
            <dl className="space-y-3">
              <div className="flex justify-between items-center">
                <dt className="text-xs font-body uppercase tracking-widest text-gray-400">Status</dt>
                <dd>
                  <span className={`text-xs font-body font-medium px-2 py-1 rounded-sm ${STATUS_VARIANT[result.status] ?? 'bg-gray-100 text-gray-700'}`}>
                    {result.status.charAt(0).toUpperCase() + result.status.slice(1)}
                  </span>
                </dd>
              </div>
              <div className="flex justify-between items-center">
                <dt className="text-xs font-body uppercase tracking-widest text-gray-400">Delivery</dt>
                <dd className="text-sm font-body text-gray-700 capitalize">{result.delivery_method}</dd>
              </div>
              <div className="flex justify-between items-center">
                <dt className="text-xs font-body uppercase tracking-widest text-gray-400">Items</dt>
                <dd className="text-sm font-body text-gray-700">{result.items_count}</dd>
              </div>
              <div className="flex justify-between items-center">
                <dt className="text-xs font-body uppercase tracking-widest text-gray-400">Placed</dt>
                <dd className="text-sm font-body text-gray-700">{result.created_at}</dd>
              </div>
            </dl>
          </div>
        )}
      </div>
    </div>
  );
}
