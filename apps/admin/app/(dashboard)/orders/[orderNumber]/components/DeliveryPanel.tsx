'use client';

import type { OrderDelivery } from '@/lib/types';
import { Badge, Button } from '@/components/ui';
import { CourierLogo } from '@/components/orders/CourierLogo';
import { cn, formatCurrency, formatDateTime, formatTimeAgo, ordinal } from '@/lib/utils';

import { COURIER_STATUS_LABEL, DELIVERED_STATUSES, PROVIDER_LABEL, SLIDER_PROVIDERS } from './courier-labels';


/**
 * What it cost to get this order out of the door.
 *
 * The customer is never shown any of this. It is here for the two questions
 * the shop actually has: is somebody coming for this box, and did we make
 * money on the delivery.
 */
export function DeliveryPanel({
  delivery,
  busy,
  onRedispatch,
  onRefresh,
  onChangeFulfilment,
  canChangeFulfilment,
  isSettled,
}: {
  delivery: OrderDelivery;
  busy: boolean;
  onRedispatch: () => void;
  onRefresh: () => void;
  onChangeFulfilment: () => void;
  /** Packed and nothing else — see `_assert_assignable` on the API side. */
  canChangeFulfilment: boolean;
  /**
   * The order is not going anywhere: cancelled, undelivered, refunded or
   * disputed. Every control that would call a driver or ask a courier for an
   * update disappears.
   *
   * `undelivered` counts. It is cancellation after the box was made, and it
   * used to offer Re-dispatch on the reasoning that the cake exists and is paid
   * for — which is how a written-off order gets a second van sent to it.
   *
   * The API refuses these too, and that is the enforcement. This only stops the
   * screen offering a button whose single possible outcome is a 409.
   */
  isSettled: boolean;
}) {
  const cost = delivery.cost_total ?? delivery.quoted_cost;
  // A marketplace rider (Careem/Talabat/…) is a fulfilment courier we only
  // OBSERVE: the aggregator dispatches and controls it, so this panel shows its
  // driver and status but offers none of the courier controls — the same "we did
  // not book this" posture as a third_party zone.
  const isAggregator = Boolean(delivery.courier?.is_aggregator);
  const isCourier = delivery.provider !== 'third_party' && !isAggregator;
  // noon Send publishes a rate card and no quotation API, so their number is
  // computed here rather than billed. Saying so stops it being read as an
  // invoice line.
  // noon Send publishes a rate card and no quotation API, so their number is
  // computed here rather than billed. Slider and Lalamove both quote, so
  // theirs is not an estimate — a Slider booking whose fare call failed
  // records no cost at all rather than a guessed one.
  const costIsEstimate = delivery.provider === 'noon_send';

  return (
    <div
      className={cn(
        'bg-white border mb-4',
        delivery.needs_attention ? 'border-red-300' : 'border-gray-200',
      )}
    >
      <div className="flex items-center gap-2 px-4 pt-4 pb-2">
        <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 flex-1">
          Fulfilment
        </p>
        {/* The courier's logo beside its name, so the carrier reads at a glance
            — the same badge the register and the order list show. */}
        {delivery.courier && <CourierLogo courier={delivery.courier} size={22} />}
        <Badge
          variant={
            delivery.provider === 'noon_send'
              ? 'success'
              : SLIDER_PROVIDERS.has(delivery.provider)
                ? 'warning'
                : isCourier
                  ? 'info'
                  : 'neutral'
          }
        >
          {PROVIDER_LABEL[delivery.provider] ?? delivery.provider}
        </Badge>
        {/* This order is not on the courier its zone chose. Worth saying: the
            provider badge alone makes a reassigned order look like one that was
            always Lalamove, and the two cost different things to explain. */}
        {delivery.original_provider &&
          delivery.original_provider !== delivery.provider && (
            <span className="text-[10px] font-body text-gray-400">
              moved from {PROVIDER_LABEL[delivery.original_provider] ?? delivery.original_provider}
            </span>
          )}
        {delivery.courier_status && (
          <Badge
            variant={
              DELIVERED_STATUSES.has(delivery.courier_status)
                ? 'success'
                : delivery.needs_attention
                  ? 'danger'
                  : 'info'
            }
          >
            {COURIER_STATUS_LABEL[delivery.courier_status] ?? delivery.courier_status}
          </Badge>
        )}
      </div>

      {delivery.last_error && (
        <p className="mx-4 mb-3 px-3 py-2 text-xs font-body text-red-700 bg-red-50 border border-red-200">
          {delivery.last_error}
        </p>
      )}

      {/* First in the list because it is what a driver or a courier's support
          desk will open a call with. */}
      {delivery.courier_reference && (
        <p className="mx-4 mb-3 px-3 py-2 text-xs font-body text-gray-600 bg-gray-50 border border-gray-200">
          Driver reference{' '}
          <span className="font-mono text-sm text-gray-900 tracking-wider">
            {delivery.courier_reference}
          </span>
        </p>
      )}

      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4 px-4 pb-4 text-xs font-body">
        <div>
          <dt className="text-gray-500">Zone</dt>
          <dd className="text-gray-800">{delivery.zone_name ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-gray-500">Charged</dt>
          <dd className="text-gray-800">
            {delivery.fee_charged !== null
              ? delivery.fee_charged > 0
                ? formatCurrency(delivery.fee_charged)
                : 'Free'
              : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">
            Courier cost{costIsEstimate && ' (est.)'}
          </dt>
          <dd className="text-gray-800">
            {cost !== null ? formatCurrency(cost) : '—'}
            {delivery.quoted_distance_m !== null && (
              <span className="text-gray-400">
                {' '}
                · {(delivery.quoted_distance_m / 1000).toFixed(1)} km
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">Margin</dt>
          <dd className={cn(delivery.margin !== null && delivery.margin < 0 ? 'text-red-600' : 'text-gray-800')}>
            {delivery.margin !== null ? formatCurrency(delivery.margin) : '—'}
          </dd>
        </div>

        {delivery.driver_name && (
          <div className="col-span-2">
            <dt className="text-gray-500">
              Driver
              {/* Said out loud, because a swapped booking used to render exactly
                  like an unswapped one — and the person on the phone to the
                  courier had no way to know they were describing somebody who
                  had dropped the job an hour ago. */}
              {delivery.driver_assignment_count > 1 && (
                <span className="ml-1 text-amber-700">
                  · reassigned ({ordinal(delivery.driver_assignment_count)} driver)
                </span>
              )}
            </dt>
            <dd className="text-gray-800">
              {delivery.driver_name}
              {delivery.driver_phone && ` · ${delivery.driver_phone}`}
              {delivery.driver_plate && ` · ${delivery.driver_plate}`}
            </dd>
            {delivery.driver_assigned_at && (
              <dd className="text-[11px] text-gray-400">
                since {formatDateTime(delivery.driver_assigned_at)}
              </dd>
            )}
          </div>
        )}
        {/* How far they still are from the kitchen — the one question somebody
            standing over a boxed order is actually asking. Absent rather than
            zero when the courier has gone quiet or the parcel is already on the
            bike; see `driver_proximity` for why silence beats a stale figure. */}
        {delivery.driver_distance_km !== null && (
          <div className="col-span-2">
            <dt className="text-gray-500">Distance from branch</dt>
            <dd className="text-gray-800">
              {/* The ETA leads where there is one — it is the number a person
                  actually plans against — and its presence is also what says
                  the figures were routed rather than estimated. The tilde on
                  the kilometres survives only on the fallback, where it is
                  earned. */}
              {delivery.driver_eta_minutes !== null ? (
                <>
                  <span className="font-medium">
                    {Math.round(delivery.driver_eta_minutes)} min
                  </span>
                  {' · '}
                  {delivery.driver_distance_km.toFixed(1)} km away
                </>
              ) : (
                <>~{delivery.driver_distance_km.toFixed(1)} km away</>
              )}
              {delivery.driver_location_at && (
                <span className="text-gray-400">
                  {' '}· as of {formatTimeAgo(delivery.driver_location_at)}
                </span>
              )}
            </dd>
            {delivery.driver_eta_minutes === null && (
              // Said rather than left to be inferred from a missing number: an
              // estimate and a routed answer look identical at a glance, and
              // only one of them accounts for the bridge.
              <dd className="text-[11px] text-gray-400">
                Straight-line estimate — no live route available
              </dd>
            )}
          </div>
        )}
        {delivery.previous_drivers.length > 0 && (
          <div className="col-span-2 sm:col-span-4">
            <dt className="text-gray-500">Previous drivers</dt>
            <dd className="text-gray-600 space-y-0.5">
              {delivery.previous_drivers.map((driver) => (
                <div key={driver.sequence} className="text-[11px]">
                  {driver.name ?? 'Unnamed'}
                  {driver.phone && ` · ${driver.phone}`}
                  {driver.replaced_at &&
                    ` · replaced ${formatDateTime(driver.replaced_at)}`}
                </div>
              ))}
            </dd>
          </div>
        )}
        {delivery.booked_at && (
          <div>
            <dt className="text-gray-500">Booked</dt>
            <dd className="text-gray-800">{formatDateTime(delivery.booked_at)}</dd>
          </div>
        )}
        {delivery.delivered_at && (
          <div>
            <dt className="text-gray-500">Delivered</dt>
            <dd className="text-gray-800">{formatDateTime(delivery.delivered_at)}</dd>
          </div>
        )}
      </dl>

      {(isCourier || delivery.pod_image_url) && (
        <div className="flex flex-wrap items-center gap-2 px-4 pb-4">
          {delivery.share_link && (
            <a
              href={delivery.share_link}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center min-h-11 md:min-h-0 text-xs font-body text-primary hover:underline"
            >
              Live tracking (internal)
            </a>
          )}
          {delivery.pod_image_url && (
            <a
              href={delivery.pod_image_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center min-h-11 md:min-h-0 text-xs font-body text-primary hover:underline"
            >
              Proof of delivery
            </a>
          )}
          <div className="flex-1" />
          {/* The couriers whose statuses only ever reach us by push, and who do
              not retry one that is lost. Lalamove pushes its own updates and
              retries them for a day, so the endpoint refuses it and a button
              here would be a 400 waiting to happen. */}
          {!isSettled &&
            (delivery.provider === 'noon_send' || SLIDER_PROVIDERS.has(delivery.provider)) &&
            delivery.courier_order_id && (
            <Button size="sm" variant="ghost" onClick={onRefresh} disabled={busy}>
              <span className="material-icons text-[14px]">sync</span>
              Check status
            </Button>
          )}
          {/* The escape hatch for a courier that will not carry this order —
              one that has gone quiet, or one that never had a chance. Where it
              may go is the zone's business, so the dialog asks the API rather
              than deciding here; this only decides whether asking is worth it.

              Shown for a booked order too, unlike the Lalamove-only button it
              replaces. A booking sitting at ASSIGNING_DRIVER for forty minutes
              is the commonest reason anybody wants this, and hiding the door
              because a booking exists was hiding it exactly when it was
              needed. */}
          {!isSettled && canChangeFulfilment && (
            <Button size="sm" variant="ghost" onClick={onChangeFulfilment} disabled={busy}>
              <span className="material-icons text-[14px]">local_shipping</span>
              Change fulfilment
            </Button>
          )}
          {isCourier && !isSettled && (
            <Button size="sm" variant="ghost" onClick={onRedispatch} disabled={busy}>
              <span className="material-icons text-[14px]">refresh</span>
              {delivery.courier_order_id ? 'Re-dispatch' : 'Dispatch now'}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
