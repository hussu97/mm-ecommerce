'use client';

import type { Order, OrderEconomics } from '@/lib/types';
import { cn, formatCurrency } from '@/lib/utils';

/**
 * What the shop actually kept, under the customer's totals.
 *
 * The block above says what was charged. This says what survived it — the van,
 * the processor, and anything sent back — because that is the number that
 * decides whether the order was worth taking, and until now working it out
 * meant opening a Stripe dashboard in another tab.
 *
 * **Two percentages, deliberately.** Against the total including fees, it says
 * how much of everything the customer handed over survives, which is the number
 * for deciding whether to run free delivery at all. Against the items alone, it
 * says what the cake earns once delivery is stripped out, which is the number
 * for pricing the cake. On a free-delivery order the two diverge sharply, and
 * showing only one invites the other to be guessed at.
 */
export function NetPayment({
  economics,
  order,
}: {
  economics: OrderEconomics;
  order: Order;
}) {
  const negative = economics.net < 0;
  return (
    <div className="border-t border-gray-200 bg-gray-50 px-4 py-3 space-y-1.5">
      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">
        What we keep
      </p>
      {/* An order has one cost of sale or the other, never both: a marketplace
          order is carried by the marketplace and MM is invoiced for no van, and
          a website order pays a courier and no commission. Showing whichever
          applies, rather than one row of dashes beside one row of money. */}
      {economics.aggregator_fee !== null ? (
        <div className="flex justify-between text-xs font-body text-gray-500">
          <span>Marketplace commission</span>
          <span>-{formatCurrency(economics.aggregator_fee)}</span>
        </div>
      ) : order.source === 'aggregator' ? (
        /* A marketplace whose rate nobody has supplied. Said plainly, with the
           screen that fixes it named — a blank here is a setup gap, and it
           makes every figure below it wrong in the flattering direction. */
        <div className="flex justify-between text-xs font-body text-amber-600">
          <span>Marketplace commission</span>
          <span title="Set it under Delivery → Estimates">Rate not set</span>
        </div>
      ) : economics.courier_cost !== null ? (
        <div className="flex justify-between text-xs font-body text-gray-500">
          <span>Courier cost</span>
          <span>-{formatCurrency(economics.courier_cost)}</span>
        </div>
      ) : (
        /* A third-party zone bills nothing per order. Saying "not itemised"
           rather than showing zero, because a third party's van is not free —
           it is just not on this order's books. */
        <div className="flex justify-between text-xs font-body text-gray-400">
          <span>Courier cost</span><span>Not itemised</span>
        </div>
      )}
      <div className="flex justify-between text-xs font-body text-gray-500">
        <span>
          Payment processing
          {economics.processing_fee_is_estimated && (
            <span className="text-gray-400"> (est.)</span>
          )}
        </span>
        <span>-{formatCurrency(economics.processing_fee)}</span>
      </div>
      {economics.refunded > 0 && (
        <div className="flex justify-between text-xs font-body text-red-600">
          <span>Refunded</span><span>-{formatCurrency(economics.refunded)}</span>
        </div>
      )}
      <div
        className={cn(
          'flex justify-between text-sm font-body font-medium pt-1 border-t border-gray-200',
          negative ? 'text-red-600' : 'text-gray-800'
        )}
      >
        <span>Net</span><span>{formatCurrency(economics.net)}</span>
      </div>
      <div className="flex justify-between text-[11px] font-body text-gray-400">
        <span>of total charged</span>
        <span>{economics.margin_on_charged !== null ? `${economics.margin_on_charged.toFixed(1)}%` : '—'}</span>
      </div>
      <div className="flex justify-between text-[11px] font-body text-gray-400">
        <span>of items value</span>
        <span>{economics.margin_on_items !== null ? `${economics.margin_on_items.toFixed(1)}%` : '—'}</span>
      </div>
      {/* The third percentage, and the one the orders list draws its tick
          against. Its denominator is the goods at *menu price* — held still on
          purpose, because a discount is a cost the shop chose to bear and
          measuring against what was actually charged would hide it inside a
          smaller base. */}
      <div
        className={cn(
          'flex justify-between text-[11px] font-body pt-1 border-t border-gray-200',
          economics.covers_direct_cost === null
            ? 'text-gray-400'
            : economics.covers_direct_cost
              ? 'text-green-700'
              : 'text-red-600',
        )}
      >
        <span>
          of menu price
          <span className="text-gray-400">
            {' '}
            (bar: {economics.direct_cost_threshold}%)
          </span>
        </span>
        <span>
          {economics.cost_cover !== null
            ? `${economics.cost_cover.toFixed(1)}%`
            : '—'}
        </span>
      </div>
    </div>
  );
}
