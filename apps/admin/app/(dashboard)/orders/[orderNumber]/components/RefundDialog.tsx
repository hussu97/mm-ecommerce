'use client';

import { useState } from 'react';

import { Button, Input } from '@/components/ui';
import { formatCurrency } from '@/lib/utils';

/**
 * Hand money back on a delivered website order, in part or in full.
 *
 * A bespoke dialog rather than the shared `useConfirm`, for the same reason
 * `ChangeFulfilmentDialog` is one: the decision needs an input and a couple of
 * numbers, and a confirm that takes a sentence cannot host them. All the money
 * math stays on the server — the dialog only caps the field at the
 * `remaining` figure the API quoted (`OrderEconomics.refundable_remaining`) and
 * lets the server refuse anything it disagrees with.
 *
 * When the amount reaches `remaining` this is a full refund, and the server will
 * move the order from delivered to cancelled. The dialog says so rather than
 * springing it on the admin after the fact.
 */
export function RefundDialog({
  remaining,
  currency,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  remaining: number;
  currency: string;
  busy: boolean;
  error: string | null;
  onConfirm: (amount: number) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState('');
  const amount = Number(value);
  const valid = value !== '' && Number.isFinite(amount) && amount > 0 && amount <= remaining;
  const isFull = valid && amount === remaining;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="refund-title"
      // Clicking away cancels; clicking the card must not. Nothing is refunded
      // either way — the refund is the button.
      onClick={onCancel}
    >
      <div
        className="bg-white border border-gray-200 w-full max-w-sm p-5"
        onClick={e => e.stopPropagation()}
      >
        <h2 id="refund-title" className="font-display text-lg text-primary mb-1">
          Refund order
        </h2>
        <p className="text-xs text-gray-500 font-body mb-4">
          Up to {formatCurrency(remaining)} can be refunded to the customer&apos;s
          card. Refunding the full amount cancels the order.
        </p>

        <Input
          type="number"
          inputMode="decimal"
          step="0.01"
          min="0"
          max={remaining}
          label={`Amount (${currency})`}
          placeholder="0.00"
          value={value}
          onChange={e => setValue(e.target.value)}
          disabled={busy}
          autoFocus
        />

        {value !== '' && !valid && (
          <p className="mt-2 text-xs font-body text-red-600">
            Enter an amount between {formatCurrency(0.01)} and{' '}
            {formatCurrency(remaining)}.
          </p>
        )}

        {isFull && (
          <p className="mt-2 text-xs font-body text-amber-800 bg-amber-50 border border-amber-200 p-2">
            This is a full refund. The order will be cancelled.
          </p>
        )}

        {error && (
          <p className="mt-2 text-xs font-body text-red-700 bg-red-50 border border-red-200 p-2">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2 mt-5">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={() => onConfirm(amount)}
            disabled={busy || !valid}
          >
            {busy
              ? 'Refunding…'
              : isFull
                ? 'Refund & cancel'
                : `Refund ${valid ? formatCurrency(amount) : ''}`.trim()}
          </Button>
        </div>
      </div>
    </div>
  );
}
