'use client';

import { useCallback, useEffect, useState } from 'react';
import { deliveryZonesApi, ApiError } from '@/lib/api';
import type { BatchGroup, Courier, CourierWrite } from '@/lib/types';
import { Spinner } from '@/components/ui';

/**
 * What the shop tells a customer their order will arrive.
 *
 * Both halves on one screen, because they are one question. Which half applies
 * to an order is decided by whether its zone is on a batch schedule:
 *
 *   * **On a schedule** — the window says when the van leaves, and the group's
 *     minutes-to-door says how long it then takes. That second number is here.
 *   * **Not on one** — every noon Send zone, every third-party zone — the
 *     courier's own promise applies, either minutes from ready or days from
 *     handover.
 *
 * Every one of these used to be a deploy. They are commercial figures: an SLA
 * is renegotiated, a route is re-timed, a partner covering Al Ain says two days
 * rather than one. Nothing quoted already moves — what the shop said out loud
 * is a record, not a derivation — so a change here changes the next promise.
 *
 * Branch holidays are the other half of the same story and live on the branches
 * screen, next to the trading hours they belong with.
 */

const COURIER_LABEL: Record<string, string> = {
  lalamove: 'Lalamove',
  noon_send: 'noon Send',
  slider: 'Slider',
  third_party: 'Third party',
};

export function DeliveryEstimates() {
  const [couriers, setCouriers] = useState<Courier[] | null>(null);
  const [groups, setGroups] = useState<BatchGroup[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [c, g] = await Promise.all([
        deliveryZonesApi.listCouriers(),
        deliveryZonesApi.listBatchGroups(),
      ]);
      setCouriers(c);
      setGroups(g);
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

  if (couriers === null || groups === null) {
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

      <section className="bg-white border border-gray-200">
        <header className="px-4 py-3 border-b border-gray-100">
          <h2 className="text-sm font-body text-gray-800">Batch runs — time to the door</h2>
          <p className="text-[11px] font-body text-gray-400 mt-1">
            How long after a run leaves that its last box is through a door. Per
            group rather than per courier, because it is a property of the route:
            the northern run crosses three emirates on the same rate card as the
            Dubai one. One number for every drop, since which stop is last is not
            knowable when the promise is made — the courier optimises the order
            after we hand it over.
          </p>
        </header>
        {groups.length === 0 ? (
          <p className="px-4 py-4 text-xs font-body text-gray-400">
            No batch groups. Every zone dispatches on its own, and the courier
            promises above are the whole of what a customer is told.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <Th>Group</Th>
                <Th>Zones</Th>
                <Th>Minutes to the door</Th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {groups.map(group => (
                <GroupRow
                  key={`${group.id}:${group.delivery_minutes_after_dispatch}:${group.is_active}`}
                  group={group}
                  busy={busy}
                  onSave={minutes =>
                    run(() =>
                      deliveryZonesApi.updateBatchGroup(group.id, {
                        delivery_minutes_after_dispatch: minutes,
                      }),
                    )
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
function MarketplaceRow({
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

/** A fee's two halves, or the amber blank that says nobody has supplied one. */
function Rate({ pair }: { pair: RatePair | null }) {
  if (pair === null) return <span className="text-amber-600">—</span>;
  return (
    <span className="text-gray-800">
      {pair.percent}%
      {pair.flat > 0 && <span> + {pair.flat.toFixed(2)}</span>}
    </span>
  );
}

function RateInput({
  percent,
  flat,
  onPercent,
  onFlat,
}: {
  percent: string;
  flat: string;
  onPercent: (value: string) => void;
  onFlat: (value: string) => void;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <input
        type="number"
        min={0}
        max={100}
        step="0.01"
        value={percent}
        onChange={e => onPercent(e.target.value)}
        placeholder="—"
        aria-label="Percentage of the order"
        className="w-16 border border-gray-300 px-2 py-1 text-xs font-body"
      />
      <span className="text-[11px] font-body text-gray-400">% +</span>
      <input
        type="number"
        min={0}
        step="0.01"
        value={flat}
        onChange={e => onFlat(e.target.value)}
        placeholder="—"
        aria-label="Flat amount per order"
        className="w-16 border border-gray-300 px-2 py-1 text-xs font-body"
      />
      <span className="text-[11px] font-body text-gray-400">flat</span>
    </span>
  );
}

/**
 * The API sends a `Decimal` as a string; the form edits a string; the console
 * renders a number. These three convert between them in one place so the
 * null-versus-zero distinction cannot be lost at one of the crossings.
 */
function asField(value: number | string | null): string {
  return value === null || value === undefined ? '' : String(Number(value));
}

/** One fee, once both of its halves are known to be present or absent. */
type RatePair = { percent: number; flat: number };

/**
 * A fee's two halves as one thing, or null when neither was supplied.
 *
 * The rule the whole feature rests on: a fee is unknown only when **both**
 * halves are null. One half set and the other null is a contract that quotes
 * only a percentage — the missing half is genuinely nothing, and treating it as
 * unknown would blank the margin on every channel with a simple contract.
 */
function asPair(
  percent: number | string | null,
  flat: number | string | null,
): RatePair | null {
  const hasPercent = percent !== null && percent !== undefined;
  const hasFlat = flat !== null && flat !== undefined;
  if (!hasPercent && !hasFlat) return null;
  return { percent: hasPercent ? Number(percent) : 0, flat: hasFlat ? Number(flat) : 0 };
}

function asWrite(value: string): number | null {
  return value.trim() === '' ? null : Number(value);
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-3 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">
      {children}
    </th>
  );
}

function describe(courier: Courier): string {
  if (courier.unbatched_promise_kind === 'minutes') {
    return courier.unbatched_promise_minutes
      ? `${courier.unbatched_promise_minutes} minutes from ready`
      : '— not set';
  }
  const days = courier.unbatched_promise_days;
  return `${days} day${days === 1 ? '' : 's'} after handover`;
}

function CourierRow({
  courier,
  busy,
  onSave,
}: {
  courier: Courier;
  busy: boolean;
  onSave: (data: CourierWrite) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<{
    unbatched_promise_kind: 'minutes' | 'next_day';
    // Never null in the form, unlike the column: the field always shows a
    // number to edit, and only the one the chosen kind reads is submitted.
    unbatched_promise_minutes: number;
    unbatched_promise_days: number;
  }>({
    unbatched_promise_kind: courier.unbatched_promise_kind,
    // Kept even while the other kind is selected, so switching back and forth
    // does not lose the number that was already there.
    unbatched_promise_minutes: courier.unbatched_promise_minutes ?? 60,
    unbatched_promise_days: courier.unbatched_promise_days,
  });

  if (!editing) {
    return (
      <tr className={courier.is_active ? '' : 'opacity-50'}>
        <td className="px-3 py-2.5 text-xs font-body text-gray-800">
          {COURIER_LABEL[courier.code] ?? courier.name}
          {!courier.is_active && <span className="text-gray-400"> · off</span>}
        </td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-500">
          {courier.zone_count || '—'}
        </td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-500">
          {courier.unbatched_promise_kind === 'minutes' ? 'A time' : 'A day'}
          {courier.supports_batching && (
            <span className="text-gray-400"> · can batch</span>
          )}
        </td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-800">
          {describe(courier)}
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

  const isMinutes = form.unbatched_promise_kind === 'minutes';
  return (
    <tr className="bg-gray-50">
      <td className="px-3 py-2.5 text-xs font-body text-gray-800">
        {COURIER_LABEL[courier.code] ?? courier.name}
      </td>
      <td className="px-3 py-2.5 text-xs font-body text-gray-500">
        {courier.zone_count || '—'}
      </td>
      <td className="px-3 py-2.5">
        <select
          value={form.unbatched_promise_kind}
          onChange={e =>
            setForm({
              ...form,
              unbatched_promise_kind: e.target.value as 'minutes' | 'next_day',
            })
          }
          className="px-2 py-1 text-xs font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary"
        >
          <option value="minutes">A time</option>
          <option value="next_day">A day</option>
        </select>
      </td>
      <td className="px-3 py-2.5">
        <span className="inline-flex items-center gap-1.5">
          <input
            value={isMinutes ? form.unbatched_promise_minutes : form.unbatched_promise_days}
            inputMode="numeric"
            onChange={e => {
              const value = Math.max(1, Number(e.target.value) || 1);
              setForm(
                isMinutes
                  ? { ...form, unbatched_promise_minutes: Math.min(1440, value) }
                  : { ...form, unbatched_promise_days: Math.min(30, value) },
              );
            }}
            className="w-16 px-2 py-1 text-xs font-body text-center bg-white border border-gray-300 rounded-sm outline-none focus:border-primary"
          />
          <span className="text-[11px] font-body text-gray-500">
            {isMinutes ? 'minutes from ready' : 'days after handover'}
          </span>
        </span>
      </td>
      <td className="px-3 py-2.5 text-right whitespace-nowrap">
        <button
          onClick={() =>
            onSave(
              // Only the number the chosen kind actually reads is sent. Writing
              // both would put a plausible figure in the column nothing uses,
              // which is how a wrong number survives long enough to be quoted.
              isMinutes
                ? {
                    unbatched_promise_kind: 'minutes',
                    unbatched_promise_minutes: form.unbatched_promise_minutes,
                  }
                : {
                    unbatched_promise_kind: 'next_day',
                    unbatched_promise_days: form.unbatched_promise_days,
                  },
            )
          }
          disabled={busy}
          className="text-[11px] font-body text-primary hover:underline mr-3"
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

function GroupRow({
  group,
  busy,
  onSave,
}: {
  group: BatchGroup;
  busy: boolean;
  onSave: (minutes: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [minutes, setMinutes] = useState(group.delivery_minutes_after_dispatch);

  return (
    <tr className={group.is_active ? '' : 'opacity-50'}>
      <td className="px-3 py-2.5 text-xs font-body text-gray-800">
        {group.name}
        {!group.is_active && <span className="text-gray-400"> · paused</span>}
      </td>
      <td className="px-3 py-2.5 text-xs font-body text-gray-400 max-w-xs truncate">
        {group.zone_names.join(' · ') || 'No zones yet'}
      </td>
      <td className="px-3 py-2.5 text-xs font-body text-gray-800">
        {editing ? (
          <input
            value={minutes}
            inputMode="numeric"
            onChange={e => setMinutes(Math.max(1, Math.min(1440, Number(e.target.value) || 1)))}
            className="w-16 px-2 py-1 text-xs font-body text-center bg-white border border-gray-300 rounded-sm outline-none focus:border-primary"
          />
        ) : (
          `${group.delivery_minutes_after_dispatch} minutes`
        )}
      </td>
      <td className="px-3 py-2.5 text-right whitespace-nowrap">
        {editing ? (
          <>
            <button
              onClick={() => onSave(minutes)}
              disabled={busy}
              className="text-[11px] font-body text-primary hover:underline mr-3"
            >
              Save
            </button>
            <button
              onClick={() => {
                setMinutes(group.delivery_minutes_after_dispatch);
                setEditing(false);
              }}
              disabled={busy}
              className="text-[11px] font-body text-gray-400 hover:underline"
            >
              Cancel
            </button>
          </>
        ) : (
          <button
            onClick={() => setEditing(true)}
            disabled={busy}
            className="text-[11px] font-body text-primary hover:underline"
          >
            Edit
          </button>
        )}
      </td>
    </tr>
  );
}
