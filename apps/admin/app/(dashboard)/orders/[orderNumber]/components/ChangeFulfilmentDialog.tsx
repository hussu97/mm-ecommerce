'use client';

import type { FulfilmentOptions, FulfilmentProvider, FulfilmentQuote } from '@/lib/types';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';

import { PROVIDER_LABEL } from './courier-labels';



/**
 * The one decision this whole flow exists for: is this delivery worth the fee.
 *
 * A bespoke dialog rather than the shared `useConfirm` every other action on
 * this page uses, because the answer depends on four numbers and a sentence
 * cannot carry them legibly. It stays local to this file — the shared confirm
 * takes a message, and stretching it to render arbitrary quote tables for a
 * single caller would be guessing at the second caller's requirements.
 *
 * It used to ask one question — Lalamove, yes or no — because that was the only
 * move the API allowed. It now picks a courier first, from whichever ones the
 * order's zone permits, and the quote table redraws underneath. The refusals
 * are rendered as sentences rather than as missing rows: a courier that is
 * absent tells somebody nothing, and the reason they need is usually "a driver
 * is already on the way", which has a button of its own.
 */
export function ChangeFulfilmentDialog({
  options,
  quote,
  target,
  expired,
  busy,
  error,
  onPick,
  onConfirm,
  onAbandon,
  onCancel,
}: {
  options: FulfilmentOptions;
  quote: FulfilmentQuote | null;
  target: FulfilmentProvider | null;
  expired: boolean;
  busy: boolean;
  error: string | null;
  onPick: (provider: FulfilmentProvider) => void;
  onConfirm: () => void;
  onAbandon: () => void;
  onCancel: () => void;
}) {
  const currency = quote?.currency || 'AED';
  const losesMoney = quote?.margin != null && quote.margin < 0;
  const priced = quote != null && quote.cost != null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="change-fulfilment-title"
      // Clicking away cancels; clicking the card must not. Nothing is booked
      // either way — the booking is the button.
      onClick={onCancel}
    >
      <div
        className="bg-white border border-gray-200 w-full max-w-sm p-5"
        onClick={e => e.stopPropagation()}
      >
        <h2
          id="change-fulfilment-title"
          className="font-display text-lg text-primary mb-1"
        >
          Change fulfilment
        </h2>
        <p className="text-xs text-gray-500 font-body mb-4">
          Currently with {PROVIDER_LABEL[options.current] ?? options.current}. The
          customer is not charged anything more — what changes is what the
          delivery costs us.
        </p>

        {options.blocked && (
          <div className="text-xs font-body text-amber-800 bg-amber-50 border border-amber-200 p-2 mb-3">
            <p>{options.blocked}</p>
            {options.must_abandon_first && (
              <button
                type="button"
                onClick={onAbandon}
                disabled={busy}
                className="mt-1.5 underline text-amber-900 disabled:opacity-50"
              >
                Abandon this booking
              </button>
            )}
          </div>
        )}

        {options.targets.length === 0 && !options.blocked && (
          // Not an error, and worth saying rather than showing an empty box:
          // it means the map has no alternate for this zone, which is a thing
          // somebody can go and change.
          <p className="text-xs font-body text-gray-500 mb-3">
            This order&apos;s zone names no other courier it may be moved to.
          </p>
        )}

        {options.targets.length > 0 && (
          <div className="flex flex-col gap-1 mb-4">
            {options.targets.map(option => (
              <label
                key={option.provider}
                className={cn(
                  'flex items-start gap-2 p-2 border text-sm font-body',
                  option.available
                    ? 'border-gray-200 cursor-pointer hover:border-primary/40'
                    : 'border-gray-100 text-gray-400',
                  target === option.provider && 'border-primary bg-primary/5',
                )}
              >
                <input
                  type="radio"
                  name="fulfilment-target"
                  disabled={!option.available || busy}
                  checked={target === option.provider}
                  onChange={() => onPick(option.provider)}
                  className="mt-0.5 accent-primary"
                />
                <span>
                  {PROVIDER_LABEL[option.provider] ?? option.provider}
                  {option.provider === options.preferred && (
                    <span className="text-gray-400"> · this zone&apos;s courier</span>
                  )}
                  {option.reason && (
                    <span className="block text-[11px] text-gray-400">
                      {option.reason}
                    </span>
                  )}
                </span>
              </label>
            ))}
          </div>
        )}

        {expired && (
          <p className="text-xs font-body text-amber-700 bg-amber-50 border border-amber-200 p-2 mb-3">
            The previous price expired. This is the current one — confirm again
            to book at it.
          </p>
        )}

        {error && (
          <p className="text-xs font-body text-red-700 bg-red-50 border border-red-200 p-2 mb-3">
            {error}
          </p>
        )}

        {priced && (
          <dl className="text-sm font-body border-t border-gray-100">
            <div className="flex justify-between py-2 border-b border-gray-100">
              <dt className="text-gray-500">
                {PROVIDER_LABEL[quote!.provider] ?? quote!.provider} quote
              </dt>
              <dd className="text-gray-900">
                {currency} {quote!.cost!.toFixed(2)}
                {quote!.distance_m !== null && (
                  <span className="text-gray-400">
                    {' '}
                    · {(quote!.distance_m / 1000).toFixed(1)} km
                  </span>
                )}
              </dd>
            </div>
            <div className="flex justify-between py-2 border-b border-gray-100">
              <dt className="text-gray-500">Customer paid</dt>
              <dd className="text-gray-900">
                {quote!.fee_charged === null
                  ? '—'
                  : `${currency} ${quote!.fee_charged.toFixed(2)}`}
              </dd>
            </div>
            <div className="flex justify-between py-2 border-b border-gray-100">
              <dt className="text-gray-500">Margin</dt>
              <dd className={cn(losesMoney ? 'text-red-600' : 'text-gray-900')}>
                {quote!.margin === null
                  ? '—'
                  : `${quote!.margin < 0 ? '−' : ''}${currency} ${Math.abs(quote!.margin).toFixed(2)}`}
              </dd>
            </div>
          </dl>
        )}

        {/* No quotation id means a rate card rather than a live quotation. Worth
            saying: the number is real but it is our arithmetic, not a figure
            noon Send has committed to. */}
        {priced && quote!.quotation_id === null && (
          <p className="text-xs font-body text-gray-500 mt-3">
            Rate-card estimate. noon Send bill what the run turns out to be.
          </p>
        )}

        {target === 'third_party' && (
          <p className="text-xs font-body text-gray-600 mt-3">
            No courier is booked and nothing further is automatic — somebody we
            already use collects the box.
            {/* The register only prints a driver slip when a courier matches a
                driver, and a third party never will. So the paper on the bag
                keeps naming whoever was carrying it before. Cheaper to say than
                to invent a print trigger for a driver who does not exist. */}
            <span className="block mt-1 text-gray-500">
              No new driver slip will print. Pull the one already on the bag —
              it names the previous courier.
            </span>
          </p>
        )}

        {/* Names the booking, then lets the exposure say what happens to it.
            It used to open with "This calls off booking X" and then append the
            exposure — which, on a booking the courier had already rejected,
            put "this calls off" directly above "there is nothing to cancel".
            Two sentences about one booking, contradicting each other, a few
            pixels apart. The id is a label now, not a claim. */}
        {quote?.cancels_booking && (
          <p className="text-xs font-body text-gray-600 mt-3">
            Booking {quote.cancels_booking}.
            {options.exposure && ` ${options.exposure.reason}`}
          </p>
        )}

        {losesMoney && (
          <p className="text-xs font-body text-red-600 mt-3">
            This delivery loses money. Move it anyway only if the order needs to
            go out today.
          </p>
        )}

        <div className="flex justify-end gap-2 mt-5">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={onConfirm}
            disabled={busy || target === null || options.blocked !== null}
          >
            {busy ? 'Moving…' : 'Confirm & move'}
          </Button>
        </div>
      </div>
    </div>
  );
}
