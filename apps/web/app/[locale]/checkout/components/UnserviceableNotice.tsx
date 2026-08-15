import { Icon } from '@/components/ui/Icon';

/**
 * The one thing on this page that stops an order.
 *
 * A pin the courier will not quote has no price, and a checkout that quietly
 * shows a fee anyway sells a delivery nobody is going to make. So it says so —
 * and, because "we cannot deliver here" is useless on its own, it says it next
 * to the two things that actually resolve it: move the pin, or collect.
 *
 * Amber rather than red. Nothing has gone wrong and nothing was lost; this
 * address simply is not one of the ones that works.
 */
export function UnserviceableNotice({
  onChangeAddress, onSwitchToPickup, t,
}: {
  onChangeAddress: () => void;
  onSwitchToPickup: () => void;
  t: (k: string, p?: Record<string, string | number>) => string;
}) {
  return (
    <div
      role="alert"
      data-field="unserviceable"
      className="mt-3 border border-amber-300 bg-amber-50/70 rounded-sm px-3.5 py-3"
    >
      <div className="flex gap-2.5">
        <Icon name="wrong_location" className="text-xl text-amber-600 shrink-0" />
        <div className="min-w-0">
          <p className="font-body text-sm text-amber-900">{t('checkout.unserviceable_title')}</p>
          <p className="font-body text-xs text-amber-800/80 mt-1 leading-relaxed">
            {t('checkout.unserviceable_body')}
          </p>
          <div className="flex flex-wrap gap-2 mt-3">
            <button
              type="button"
              onClick={onChangeAddress}
              className="font-body text-xs px-3 py-1.5 border border-amber-400 text-amber-900 rounded-sm hover:bg-amber-100 transition-colors"
            >
              {t('checkout.unserviceable_change')}
            </button>
            <button
              type="button"
              onClick={onSwitchToPickup}
              className="font-body text-xs px-3 py-1.5 text-amber-800 underline underline-offset-2 hover:text-amber-900 transition-colors"
            >
              {t('checkout.unserviceable_pickup')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
