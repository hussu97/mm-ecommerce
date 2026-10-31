'use client';

/**
 * Below the statement: stock bought on the window's closed purchase orders
 * that the P&L has not expensed yet (`/profit-loss/purchase-orders`).
 *
 *   Received − Consumed = Net,  Net = Used elsewhere + In stock
 *
 * Every figure is the API's — it traces each PO's stock through batches,
 * transfers and returns to the orders this P&L counts — and this renders it.
 * Its own request, so the statement above never waits on the trace. Branch and
 * entity pick the POs; the channel filter does not apply (stock is not bought
 * per channel), and the section says so when one is set.
 *
 * Collapsed until asked for: the trace is the slowest request the console
 * makes (seconds, on the single API worker the statement shares), and most
 * visits to the P&L never look at it. It is fetched when the section opens,
 * again when a filter changes while it is open, and not again for a reopen
 * on the filters it already has.
 */

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { profitLossApi, type PnlPurchaseOrders } from '@/lib/api';
import { LoadError, Spinner } from '@/components/ui';
import { cn, formatCurrency } from '@/lib/utils';

export function PurchaseOrderStock({
  dateFrom,
  dateTo,
  branchIds,
  entityIds,
  channelFiltered,
}: {
  dateFrom: string;
  dateTo: string;
  branchIds: string[];
  entityIds: string[];
  channelFiltered: boolean;
}) {
  const [data, setData] = useState<PnlPurchaseOrders | null>(null);
  // The filters `data` was fetched for: a reopen on the same ones reuses it.
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // `shown`: the section, and so the trace. `open`: its per-PO table.
  const [shown, setShown] = useState(false);
  const [open, setOpen] = useState(false);

  const key = JSON.stringify([dateFrom, dateTo, branchIds, entityIds]);
  useEffect(() => {
    if (!shown || loadedKey === key) return;
    let live = true;
    setLoading(true);
    setError('');
    profitLossApi
      .purchaseOrders({
        date_from: dateFrom,
        date_to: dateTo,
        branch_ids: branchIds.length ? branchIds : undefined,
        legal_entity_ids: entityIds.length ? entityIds : undefined,
      })
      .then(r => {
        if (!live) return;
        setData(r);
        setLoadedKey(key);
      })
      .catch(e => live && setError((e as Error).message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, shown]);

  return (
    <section className="border border-gray-200 bg-white p-4">
      <h2 className="mb-1 font-display text-base text-gray-800">
        Purchase orders received · not yet in COGS
      </h2>
      <p className="mb-3 text-[11px] font-body text-gray-400">
        The stock lines of the purchase orders closed in this window, net of recoverable VAT
        like COGS. Misc lines (rent, salaries, supplies) are not here: they are in the statement
        above already. <strong className="font-medium">Consumed</strong> is what orders this
        P&amp;L counts have drawn of that stock to date — as it was, or inside a batch or a
        transfer — and is inside a COGS line already, so it comes off rather than being counted
        twice.
        {channelFiltered && ' The channel filter does not apply here: stock is not bought per channel.'}
      </p>

      <button
        type="button"
        onClick={() => setShown(s => !s)}
        aria-expanded={shown}
        className="mb-3 inline-flex min-h-[var(--tap-min)] items-center text-xs font-body text-gray-500 underline hover:text-gray-700 md:min-h-0"
      >
        {shown ? 'Hide' : 'Show'} purchase-order stock
      </button>

      {shown && (
        <>
          <LoadError message={error} />
          {!data && !error ? (
            <div className="flex justify-center py-6">
              <Spinner />
            </div>
          ) : data ? (
            <div className={cn(loading && 'opacity-60')}>
              {data.purchase_orders.length === 0 ? (
                <p className="text-xs font-body text-gray-400">
                  No purchase order with stock lines was closed in this window.
                </p>
              ) : (
                <>
                  <dl className="max-w-md space-y-1 text-xs font-body">
                    <div className="flex justify-between text-gray-600">
                      <dt>Received ({data.purchase_orders.length} purchase orders)</dt>
                      <dd className="whitespace-nowrap tabular-nums">{formatCurrency(data.received)}</dd>
                    </div>
                    <div className="flex justify-between text-gray-600">
                      <dt>Consumed in P&amp;L orders, to date</dt>
                      <dd className="whitespace-nowrap tabular-nums">
                        {data.consumed === 0 ? formatCurrency(0) : `−${formatCurrency(data.consumed)}`}
                      </dd>
                    </div>
                    <div className="flex justify-between border-t border-gray-200 pt-1 font-medium text-gray-800">
                      <dt>Net · in no COGS</dt>
                      <dd className="whitespace-nowrap tabular-nums">{formatCurrency(data.net)}</dd>
                    </div>
                    <div className="flex justify-between pl-3 text-[11px] text-gray-400">
                      <dt>Still in stock, raw or in goods not sold yet</dt>
                      <dd className="whitespace-nowrap tabular-nums">{formatCurrency(data.in_stock)}</dd>
                    </div>
                    <div className="flex justify-between pl-3 text-[11px] text-gray-400">
                      <dt>Used elsewhere · count shortfalls, internal use, unsold orders</dt>
                      <dd className="whitespace-nowrap tabular-nums">{formatCurrency(data.used_elsewhere)}</dd>
                    </div>
                  </dl>

                  <button
                    type="button"
                    onClick={() => setOpen(o => !o)}
                    aria-expanded={open}
                    className="mt-3 inline-flex min-h-[var(--tap-min)] items-center text-xs font-body text-gray-500 underline hover:text-gray-700 md:min-h-0"
                  >
                    {open ? 'Hide' : 'Show'} the {data.purchase_orders.length} purchase orders
                  </button>

                  {open && (
                    <div className="mt-3 overflow-x-auto">
                      <table className="w-full min-w-[760px] text-xs font-body">
                        <thead>
                          <tr className="border-b border-gray-200 text-[11px] uppercase tracking-wider text-gray-400">
                            <th className="px-2 py-2 text-left font-normal">Received on</th>
                            <th className="px-2 py-2 text-left font-normal">Purchase order</th>
                            <th className="px-2 py-2 text-left font-normal">Supplier</th>
                            <th className="px-2 py-2 text-left font-normal">Branch</th>
                            <th className="px-2 py-2 text-right font-normal">Received</th>
                            <th className="px-2 py-2 text-right font-normal">Consumed</th>
                            <th className="px-2 py-2 text-right font-normal">Net</th>
                            <th className="px-2 py-2 text-right font-normal">In stock</th>
                            <th className="px-2 py-2 text-right font-normal">Used elsewhere</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.purchase_orders.map(po => (
                            <tr key={po.purchase_order_id} className="border-b border-gray-100 last:border-0 text-gray-600">
                              <td className="whitespace-nowrap px-2 py-1.5">{po.business_date}</td>
                              <td className="whitespace-nowrap px-2 py-1.5">
                                <Link href={`/purchase-orders/${po.purchase_order_id}`} className="text-primary hover:underline">
                                  {po.reference}
                                </Link>
                              </td>
                              <td className="px-2 py-1.5">{po.supplier}</td>
                              <td className="px-2 py-1.5">{po.branch}</td>
                              <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums">{formatCurrency(po.received)}</td>
                              <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums">
                                {po.consumed === 0 ? formatCurrency(0) : `−${formatCurrency(po.consumed)}`}
                              </td>
                              <td className="whitespace-nowrap px-2 py-1.5 text-right font-medium tabular-nums text-gray-800">
                                {formatCurrency(po.net)}
                              </td>
                              <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums text-gray-400">{formatCurrency(po.in_stock)}</td>
                              <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums text-gray-400">{formatCurrency(po.used_elsewhere)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
