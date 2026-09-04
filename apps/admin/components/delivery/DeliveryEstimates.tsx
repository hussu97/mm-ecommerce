'use client';

import { useCallback, useEffect, useState } from 'react';
import { deliveryZonesApi, ApiError } from '@/lib/api';
import type { Courier } from '@/lib/types';
import { Spinner } from '@/components/ui';

import { CourierRow } from './CourierRow';
import { MarketplaceRow } from './MarketplaceRow';
import { Th } from './rate-fields';

/**
 * What the shop tells a customer their order will arrive.
 *
 * Every zone's promise is its courier's own: either minutes from the order
 * being ready, for a courier we dispatch ourselves, or days from handover, for
 * one that collects on its own schedule.
 *
 * Every one of these used to be a deploy. They are commercial figures: an SLA
 * is renegotiated, a route is re-timed, a partner covering Al Ain says two days
 * rather than one. Nothing quoted already moves — what the shop said out loud
 * is a record, not a derivation — so a change here changes the next promise.
 *
 * Branch holidays are the other half of the same story and live on the branches
 * screen, next to the trading hours they belong with.
 */

export function DeliveryEstimates() {
  const [couriers, setCouriers] = useState<Courier[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const c = await deliveryZonesApi.listCouriers();
      setCouriers(c);
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the estimates.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await action();
      await load();
    } catch (err) {
      // A refused promise comes back as a sentence saying which combination is
      // impossible — worth showing verbatim rather than replacing with "failed".
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  // The table has always listed every `couriers` row, which was fine while they
  // were all couriers. The marketplaces joined the table when they became
  // carrier badges, and they have been sitting in the promises list ever since
  // being offered a delivery promise that nothing reads — MM dispatches none of
  // them. Split here rather than filtering in one place and forgetting the
  // other: each half now has the columns that mean something to it.
  const dispatched = (couriers ?? []).filter(c => !c.is_aggregator);
  const marketplaces = (couriers ?? []).filter(c => c.is_aggregator);

  if (couriers === null) {
    return (
      <div className="py-10 flex justify-center">
        {error ? (
          <p className="text-xs font-body text-red-600">{error}</p>
        ) : (
          <Spinner />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && (
        <p className="px-3 py-2 text-xs font-body text-red-700 bg-red-50 border border-red-200">
          {error}
        </p>
      )}

      <section className="bg-white border border-gray-200">
        <header className="px-4 py-3 border-b border-gray-100">
          <h2 className="text-sm font-body text-gray-800">Courier promises</h2>
          <p className="text-[11px] font-body text-gray-400 mt-1">
            What a zone is quoted when it is not waiting for a shared run. A
            courier we dispatch ourselves promises minutes from the order being
            ready; one that collects on its own schedule promises days from
            handover, and never an hour — its van is not ours to name a time for.
          </p>
        </header>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <Th>Courier</Th>
              <Th>Zones</Th>
              <Th>Promises</Th>
              <Th>Estimate</Th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {dispatched.map(courier => (
              <CourierRow
                key={`${courier.code}:${courier.unbatched_promise_kind}:${courier.unbatched_promise_minutes}:${courier.unbatched_promise_days}:${courier.is_active}`}
                courier={courier}
                busy={busy}
                onSave={data => run(() => deliveryZonesApi.updateCourier(courier.code, data))}
              />
            ))}
          </tbody>
        </table>
      </section>

      <section className="bg-white border border-gray-200">
        <header className="px-4 py-3 border-b border-gray-100">
          <h2 className="text-sm font-body text-gray-800">Marketplace fees</h2>
          <p className="text-[11px] font-body text-gray-400 mt-1">
            What each aggregator takes off an order. Both are percentages
            <strong className="font-normal text-gray-500"> before VAT</strong>,
            the way the contracts are written — the 5% is added when the fee is
            stamped onto the order. A blank is not a zero: it means the rate has
            not been supplied, and those orders show no net at all rather than
            appearing to have kept every dirham.
          </p>
          <p className="text-[11px] font-body text-gray-400 mt-1">
            Changing a rate is not retrospective. Every order already taken
            keeps the fee it was stamped with, so renegotiating a contract
            cannot rewrite last month&rsquo;s margins.
          </p>
        </header>
        {marketplaces.length === 0 ? (
          <p className="px-4 py-4 text-xs font-body text-gray-400">
            No marketplace channels configured.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <Th>Marketplace</Th>
                <Th>Commission</Th>
                <Th>Payment fee</Th>
                <Th>Total off an order</Th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {marketplaces.map(courier => (
                <MarketplaceRow
                  key={`${courier.code}:${courier.commission_percent}:${courier.payment_fee_percent}`}
                  courier={courier}
                  busy={busy}
                  onSave={data =>
                    run(() => deliveryZonesApi.updateCourier(courier.code, data))
                  }
                />
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
