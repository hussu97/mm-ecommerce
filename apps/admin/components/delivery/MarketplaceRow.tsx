'use client';

import { useState } from 'react';
import { Rate, RateInput, asField, asPair, asWrite } from './rate-fields';
import type { Courier, CourierWrite } from '@/lib/types';


/**
 * One marketplace and what it takes off an order.
 *
 * **A blank field is a real value here**, and the whole row is built around
 * saying so. `''` submits as `null` — "nobody has told us this rate" — and `0`
 * submits as zero, "this channel takes nothing". Every screen downstream reads
 * the difference: a null leaves the order's net unknown and shows a dash, while
 * a zero claims the order kept everything. A form that collapsed the two would
 * put a 25% commission back on the margin report as profit.
 */
export function MarketplaceRow({
  courier,
  busy,
  onSave,
}: {
  courier: Courier;
  busy: boolean;
  onSave: (data: CourierWrite) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    commissionPct: asField(courier.commission_percent),
    commissionFlat: asField(courier.commission_fixed),
    paymentPct: asField(courier.payment_fee_percent),
    paymentFlat: asField(courier.payment_fee_fixed),
  });

  const commission = asPair(courier.commission_percent, courier.commission_fixed);
  const payment = asPair(courier.payment_fee_percent, courier.payment_fee_fixed);
  // Only summable when both fees are known, and only as a percentage when
  // neither carries a flat part — a flat amount cannot be added to a percentage
  // without a basket to measure it against. One rate plus an unknown is not a
  // total either, and printing the half we have as though it were the whole
  // would understate exactly the channels whose setup is unfinished.
  const total =
    commission && payment && !commission.flat && !payment.flat
      ? `${commission.percent + payment.percent}% + VAT`
      : commission && payment
        ? 'Mixed — see the two columns'
        : null;

  if (!editing) {
    return (
      <tr className={courier.is_active ? '' : 'opacity-50'}>
        <td className="px-3 py-2.5 text-xs font-body text-gray-800">
          {courier.name}
          {!courier.is_active && <span className="text-gray-400"> · off</span>}
        </td>
        <td className="px-3 py-2.5 text-xs font-body">
          <Rate pair={commission} />
        </td>
        <td className="px-3 py-2.5 text-xs font-body">
          <Rate pair={payment} />
        </td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-800">
          {total === null ? <span className="text-amber-600">Not set</span> : total}
        </td>
        <td className="px-3 py-2.5 text-right">
          <button
            onClick={() => setEditing(true)}
            disabled={busy}
            className="text-[11px] font-body text-primary hover:underline"
          >
            Edit
          </button>
        </td>
      </tr>
    );
  }

  return (
    <tr className="bg-gray-50">
      <td className="px-3 py-2.5 text-xs font-body text-gray-800">{courier.name}</td>
      <td className="px-3 py-2.5">
        <RateInput
          percent={form.commissionPct}
          flat={form.commissionFlat}
          onPercent={v => setForm(f => ({ ...f, commissionPct: v }))}
          onFlat={v => setForm(f => ({ ...f, commissionFlat: v }))}
        />
      </td>
      <td className="px-3 py-2.5">
        <RateInput
          percent={form.paymentPct}
          flat={form.paymentFlat}
          onPercent={v => setForm(f => ({ ...f, paymentPct: v }))}
          onFlat={v => setForm(f => ({ ...f, paymentFlat: v }))}
        />
      </td>
      <td className="px-3 py-2.5 text-[11px] font-body text-gray-400">
        Both blank means &ldquo;not known&rdquo;
      </td>
      <td className="px-3 py-2.5 text-right space-x-3 whitespace-nowrap">
        <button
          onClick={() => {
            onSave({
              commission_percent: asWrite(form.commissionPct),
              commission_fixed: asWrite(form.commissionFlat),
              payment_fee_percent: asWrite(form.paymentPct),
              payment_fee_fixed: asWrite(form.paymentFlat),
            });
            setEditing(false);
          }}
          disabled={busy}
          className="text-[11px] font-body text-primary hover:underline"
        >
          Save
        </button>
        <button
          onClick={() => setEditing(false)}
          disabled={busy}
          className="text-[11px] font-body text-gray-400 hover:underline"
        >
          Cancel
        </button>
      </td>
    </tr>
  );
}
