'use client';

/**
 * One order's profit & loss, GMV down to PC3, as the API computed it.
 *
 * Every figure is net of VAT and comes from `/orders/{n}/pnl` — the same
 * expressions the orders list's percentage and the P&L page's totals are built
 * from — so this breakdown is the working behind the number on the row, never a
 * second opinion. Nothing here adds anything up (canon rule 10).
 */

import { useEffect, useState } from 'react';
import { ordersApi, type OrderPnl } from '@/lib/api';
import { Spinner } from '@/components/ui';
import { cn, formatCurrency } from '@/lib/utils';

type Tone = 'plain' | 'cost' | 'subtotal';

function Line({
  label,
  value,
  tone = 'plain',
  pct,
  hint,
  indent,
}: {
  label: string;
  value: number | null;
  tone?: Tone;
  pct?: number | null;
  hint?: string;
  indent?: boolean;
}) {
  const shown =
    value === null ? '—' : tone === 'cost' && value !== 0 ? `−${formatCurrency(value)}` : formatCurrency(value);
  return (
    <div
      className={cn(
        'flex items-baseline justify-between gap-3 text-xs font-body',
        tone === 'subtotal'
          ? 'border-t border-gray-200 pt-1 font-medium text-gray-800'
          : 'text-gray-500',
        indent && 'pl-3 text-[11px] text-gray-400',
      )}
      title={hint}
    >
      <span>{label}</span>
      <span className="tabular-nums">
        {shown}
        {pct !== undefined && (
          <span className="ml-2 text-[11px] text-gray-400">
            {pct === null ? '' : `${pct.toFixed(1)}%`}
          </span>
        )}
      </span>
    </div>
  );
}

export function PnlLines({ pnl }: { pnl: OrderPnl }) {
  const negative = pnl.pc3 < 0;
  return (
    <div className="space-y-1">
      {!pnl.is_sale && (
        <p className="text-[11px] font-body text-amber-700 pb-1">
          Cancelled, but the marketplace still charged for it — no revenue, only the charge.
        </p>
      )}
      <Line label="GMV (before discounts)" value={pnl.gmv} />
      {pnl.refunds !== 0 && <Line label="Refunds" value={pnl.refunds} tone="cost" />}
      <Line label="Net revenue" value={pnl.net_revenue} tone="subtotal" />
      <Line
        label="COGS"
        value={pnl.cogs}
        tone="cost"
        hint={
          pnl.cogs_missing
            ? 'No stock movement was recorded for this order (before inventory go-live, or it never posted), so its cost of goods is unknown — not zero.'
            : pnl.cogs_provisional > 0
              ? `${formatCurrency(pnl.cogs_provisional)} of this is priced provisionally, until a purchase order prices the stock.`
              : undefined
        }
      />
      <Line label="PC1" value={pnl.pc1} tone="subtotal" pct={pnl.pc1_pct} />
      <Line label="Payment fees" value={pnl.payment_fees} tone="cost" />
      <Line label="Aggregator & delivery fees" value={pnl.aggregator_and_delivery_fees} tone="cost" />
      {pnl.commission !== 0 && <Line label="Commission" value={pnl.commission} tone="cost" indent />}
      {pnl.marketplace_fees !== 0 && (
        <Line label="Loyalty / Pro / subsidy fees" value={pnl.marketplace_fees} tone="cost" indent />
      )}
      {pnl.delivery_cost !== 0 && <Line label="Our courier" value={pnl.delivery_cost} tone="cost" indent />}
      <Line label="Misc fees" value={pnl.misc_fees} tone="cost" />
      {pnl.cancellation_charges !== 0 && (
        <Line label="Cancellation charges" value={pnl.cancellation_charges} tone="cost" indent />
      )}
      <Line label="PC2" value={pnl.pc2} tone="subtotal" pct={pnl.pc2_pct} />
      <Line label="Discounts" value={pnl.discounts} tone="cost" />
      <div
        className={cn(
          'flex items-baseline justify-between border-t border-gray-300 pt-1 text-sm font-body font-medium',
          negative ? 'text-red-600' : 'text-gray-900',
        )}
      >
        <span>PC3</span>
        <span className="tabular-nums">
          {formatCurrency(pnl.pc3)}
          <span className="ml-2 text-[11px] text-gray-400">
            {pnl.pc3_pct === null ? '' : `${pnl.pc3_pct.toFixed(1)}%`}
          </span>
        </span>
      </div>
      <div className="pt-2 text-[11px] font-body text-gray-400 space-y-0.5">
        <p>
          Net of VAT · output VAT {formatCurrency(pnl.output_vat)} · input VAT reclaimed{' '}
          {formatCurrency(pnl.input_vat)}
        </p>
        {pnl.is_sale && pnl.cogs_missing && (
          <p className="text-amber-700">COGS not recorded for this order.</p>
        )}
        {pnl.fees_pending && (
          <p className="text-amber-700">
            A fee is still to land (marketplace commission before its statement, or a courier
            invoice), so PC2 and PC3 will fall when it does.
          </p>
        )}
      </div>
    </div>
  );
}

/** Loads and renders one order's P&L; used on the order page and the list dialog. */
export function OrderPnlPanel({ orderNumber, reloadKey }: { orderNumber: string; reloadKey?: string }) {
  const [pnl, setPnl] = useState<OrderPnl | null | undefined>(undefined);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    ordersApi
      .pnl(orderNumber)
      .then(p => {
        if (!live) return;
        setError('');
        setPnl(p);
      })
      .catch(e => live && setError((e as Error).message));
    return () => {
      live = false;
    };
  }, [orderNumber, reloadKey]);

  if (error) return <p className="text-xs font-body text-red-600">{error}</p>;
  if (pnl === undefined) {
    return (
      <div className="flex justify-center py-4">
        <Spinner />
      </div>
    );
  }
  if (pnl === null) {
    return (
      <p className="text-xs font-body text-gray-400">
        Not in the P&amp;L — the order is still in flight, or was cancelled without a charge.
      </p>
    );
  }
  return <PnlLines pnl={pnl} />;
}

/** The orders list's pop-up: the working behind the row's percentage. */
export function OrderPnlDialog({
  orderNumber,
  onClose,
}: {
  orderNumber: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="pnl-title"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm border border-gray-200 bg-white p-5"
        onClick={e => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 id="pnl-title" className="font-display text-lg text-primary">
            Profit &amp; loss · {orderNumber}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
            aria-label="Close"
          >
            <span className="material-icons text-[18px]">close</span>
          </button>
        </div>
        <OrderPnlPanel orderNumber={orderNumber} />
      </div>
    </div>
  );
}
