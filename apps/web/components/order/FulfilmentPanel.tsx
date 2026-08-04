'use client';

/**
 * Everything the storefront knows about how an order reaches its customer.
 *
 * One component for both screens that ask the question. The account page has
 * the whole order to show around it and the guest tracking page has almost
 * nothing, but the answer to "when does it arrive, where do I collect it, can I
 * watch it move" is identical — and it was the two of them drifting that left
 * the tracking page showing four fields and no estimate at all.
 *
 * It renders from `Fulfilment` alone. Every decision behind those fields —
 * which estimate applies, whether a tracking link is worth showing yet — was
 * made by `fulfilment_service` on the server, so there is no branch here that
 * could disagree with the email about the same order.
 */

import type { Fulfilment, FulfilmentStage, PickupBranch } from '@/lib/types';
import { useTranslation } from '@/lib/i18n/TranslationProvider';

/** The journey, per method. Mirrors `email_service._timeline` deliberately. */
const PICKUP_STEPS: FulfilmentStage[] = ['preparing', 'ready', 'collected'];
const COURIER_STEPS: FulfilmentStage[] = ['preparing', 'ready', 'on_the_way', 'delivered'];
const HANDOVER_STEPS: FulfilmentStage[] = ['preparing', 'ready', 'delivered'];

const STEP_ICON: Record<FulfilmentStage, string> = {
  preparing: 'bakery_dining',
  ready: 'inventory_2',
  on_the_way: 'local_shipping',
  collected: 'check_circle',
  delivered: 'check_circle',
  undelivered: 'error_outline',
  settled: 'cancel',
};

/**
 * Locale-aware, and on the shop's clock rather than the reader's.
 *
 * A customer looking at this from a hotel in London still has to collect a cake
 * in Sharjah, so the hour on screen is the hour at the counter.
 */
function formatMoment(
  iso: string,
  precision: 'time' | 'day' | 'exact' | null,
  locale: string,
): string {
  const date = new Date(iso);
  const tz = 'Asia/Dubai';
  const day = new Intl.DateTimeFormat(locale === 'ar' ? 'ar-AE' : 'en-AE', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: tz,
  }).format(date);

  if (precision === 'day') return day;

  const time = new Intl.DateTimeFormat(locale === 'ar' ? 'ar-AE' : 'en-AE', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: tz,
  }).format(date);
  return `${day}, ${time}`;
}

function stepsFor(fulfilment: Fulfilment): FulfilmentStage[] {
  if (fulfilment.method === 'pickup') return PICKUP_STEPS;
  // Without a courier reporting back there is no honest middle step: the box is
  // packed, and the next thing anybody knows is that it arrived. Showing a
  // greyed-out "out for delivery" nobody will ever tick reads as a stage that
  // got skipped.
  return fulfilment.courier_managed ? COURIER_STEPS : HANDOVER_STEPS;
}

export function BranchCard({ branch }: { branch: PickupBranch }) {
  const { t, locale } = useTranslation();
  const isAr = locale === 'ar';
  const name = (isAr && branch.name_ar) || branch.name;
  const address = (isAr && branch.address_ar) || branch.address;
  const city = (isAr && branch.city_ar) || branch.city;

  return (
    <div className="border border-gray-200 p-4">
      <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-2">
        {t('order.collect_from')}
      </h2>
      <p className="font-display text-lg text-primary leading-tight">{name}</p>
      {address && <p className="text-sm text-gray-700 font-body mt-2">{address}</p>}
      {city && <p className="text-sm text-gray-400 font-body">{city}</p>}

      <dl className="mt-3 space-y-1">
        <div className="flex gap-3 text-sm font-body">
          <dt className="text-gray-400 w-20 shrink-0">{t('order.branch_open')}</dt>
          <dd className="text-gray-700">
            {branch.opening_from} – {branch.opening_to}
          </dd>
        </div>
        {branch.phone && (
          <div className="flex gap-3 text-sm font-body">
            <dt className="text-gray-400 w-20 shrink-0">{t('order.branch_phone')}</dt>
            <dd>
              <a href={`tel:${branch.phone.replace(/\s/g, '')}`} className="text-primary hover:underline">
                {branch.phone}
              </a>
            </dd>
          </div>
        )}
      </dl>

      {branch.maps_url && (
        <a
          href={branch.maps_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 inline-flex items-center gap-1.5 text-sm font-body text-primary hover:underline"
        >
          <span className="material-icons text-[18px]">place</span>
          {t('order.open_in_maps')}
        </a>
      )}
    </div>
  );
}

export function FulfilmentPanel({
  fulfilment,
  className = '',
}: {
  fulfilment: Fulfilment | null | undefined;
  className?: string;
}) {
  const { t, locale } = useTranslation();
  if (!fulfilment) return null;

  const { stage, estimated_at, precision, tracking_url, branch } = fulfilment;
  const isPickup = fulfilment.method === 'pickup';
  const steps = stepsFor(fulfilment);
  const reached = steps.indexOf(stage);

  // The label has to change with the stage or it lies: "estimated delivery" over
  // a timestamp from an hour ago is not an estimate, it is a record.
  const estimateLabel =
    stage === 'collected'
      ? t('order.estimate_collected')
      : stage === 'delivered'
        ? t('order.estimate_delivered')
        : stage === 'ready' && isPickup
          ? t('order.estimate_ready_since')
          : stage === 'on_the_way'
            ? t('order.estimate_arriving')
            : isPickup
              ? t('order.estimate_ready')
              : t('order.estimate_delivery');

  const estimateNote =
    stage === 'on_the_way'
      ? t('order.estimate_note_rider')
      : precision === 'day' && stage !== 'delivered' && stage !== 'collected'
        ? t('order.estimate_note_day')
        : '';

  const showTimeline = stage !== 'settled' && stage !== 'undelivered';

  return (
    <div className={className}>
      {stage === 'undelivered' && (
        <div className="mb-4 bg-amber-50 border border-amber-200 p-4">
          <p className="text-xs font-medium text-amber-800 uppercase tracking-widest mb-1">
            {t('order.undelivered_title')}
          </p>
          <p className="text-sm text-amber-700 font-body">{t('order.undelivered_body')}</p>
        </div>
      )}

      {estimated_at && (
        <div className="mb-4 border border-tertiary/60 bg-[#fbf7f5] p-4">
          <p className="text-xs font-body uppercase tracking-widest text-primary/70 mb-1">
            {estimateLabel}
          </p>
          <p className="font-display text-xl text-primary leading-tight">
            {formatMoment(estimated_at, precision, locale)}
          </p>
          {estimateNote && <p className="text-sm text-gray-500 font-body mt-1.5">{estimateNote}</p>}
        </div>
      )}

      {tracking_url && (
        <a
          href={tracking_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mb-4 flex items-center justify-center gap-2 w-full bg-primary text-white font-body text-sm uppercase tracking-widest py-3.5 hover:bg-primary/90 transition-colors"
        >
          <span className="material-icons text-[18px]">near_me</span>
          {t('order.track_live')}
        </a>
      )}

      {/* Before a rider has it there is no map to open, but saying a link is
          coming is the difference between an empty panel and a promise. */}
      {!tracking_url && fulfilment.courier_managed && stage !== 'delivered' && stage !== 'settled' && (
        <p className="mb-4 text-sm text-gray-500 font-body">{t('order.track_live_pending')}</p>
      )}

      {showTimeline && (
        <div className="mb-4 p-5 border border-gray-100 bg-gray-50">
          <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-4">
            {t('order.order_progress')}
          </h2>
          <ol className="space-y-3">
            {steps.map((step, idx) => {
              const done = reached >= idx;
              const when =
                step === 'ready'
                  ? fulfilment.packed_at
                  : step === 'on_the_way'
                    ? fulfilment.picked_up_at
                    : step === 'delivered' || step === 'collected'
                      ? fulfilment.delivered_at
                      : null;
              return (
                <li key={step} className="flex items-center gap-3">
                  <span
                    className={`material-icons text-[20px] ${done ? 'text-primary' : 'text-gray-300'}`}
                  >
                    {done ? 'check_circle' : STEP_ICON[step]}
                  </span>
                  <span
                    className={`text-sm font-body flex-1 ${done ? 'text-gray-800' : 'text-gray-400'}`}
                  >
                    {t(`order.step_${isPickup ? 'pickup_' : ''}${step}`)}
                  </span>
                  {done && when && (
                    <span className="text-xs font-body text-gray-400">
                      {formatMoment(when, 'time', locale)}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      )}

      {branch && <BranchCard branch={branch} />}
    </div>
  );
}
