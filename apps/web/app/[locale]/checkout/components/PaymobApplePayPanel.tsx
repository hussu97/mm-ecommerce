'use client';

/**
 * The second tap of the embedded-SDK Apple Pay path (see `usePaymobApplePay`).
 *
 * The order is already written by the time this is doing anything useful; what
 * it holds is the element the SDK draws its own Apple Pay button into, and one
 * line telling the customer that the button is the next thing to press. Drawn
 * once per page — the SDK mounts into a single element id — which is why the
 * checkout renders it in place of *both* of its action-button slots rather than
 * inside each of them.
 *
 * The SDK's element is left empty on purpose: everything in it belongs to the
 * SDK, and React must not render children there or it will fight the SDK over
 * the same node.
 */

import { useEffect, useRef } from 'react';

import { Spinner } from '@/components/ui/Spinner';
import { withFallback } from '@/lib/i18n/fallback';
import type { PaymobApplePayStatus } from '../hooks/usePaymobApplePay';

type TFunction = (key: string, params?: Record<string, string | number>) => string;

export function PaymobApplePayPanel({
  elementId,
  status,
  onPayAnotherWay,
  t,
}: {
  /** The id the hook mounts the SDK into. */
  elementId: string;
  status: PaymobApplePayStatus;
  /** Step off Apple Pay: the order stays, and is paid for on the card page instead. */
  onPayAnotherWay: () => void;
  t: TFunction;
}) {
  const ref = useRef<HTMLDivElement>(null);

  // The button that opened this is at the bottom of a phone's screen, and the
  // panel it opened may not be — bring it into view so the next tap is visible.
  useEffect(() => {
    ref.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, []);

  const preparing = status !== 'ready';

  return (
    <div ref={ref} className="border border-gray-200 rounded-sm px-3.5 py-3.5 space-y-3" aria-live="polite">
      <p className="flex items-center gap-2 font-body text-sm text-gray-700">
        {preparing && <Spinner size="sm" />}
        <span>
          {preparing
            ? withFallback(t, 'checkout.apple_pay_preparing', 'Getting Apple Pay ready…')
            : withFallback(
                t,
                'checkout.apple_pay_tap_to_pay',
                'Your order is saved. Tap the Apple Pay button to pay.',
              )}
        </span>
      </p>

      {/* The SDK's element. Empty, and never given children — see above. */}
      <div id={elementId} className="min-h-[2.875rem]" />

      {!preparing && (
        <button
          type="button"
          onClick={onPayAnotherWay}
          className="font-body text-xs text-primary hover:underline"
        >
          {withFallback(t, 'checkout.apple_pay_pay_another_way', 'Pay by card instead')}
        </button>
      )}
    </div>
  );
}
