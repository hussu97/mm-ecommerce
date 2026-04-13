'use client';

import { useTranslation } from '@/lib/i18n/TranslationProvider';

interface DeliveryCalculatorProps {
  deliveryMethod: 'delivery' | 'pickup';
  region: string;
  effectiveSubtotal: number;
  deliveryFee: number;
  freeThreshold: number;
  onChange: (method: 'delivery' | 'pickup') => void;
}

export function DeliveryCalculator({
  deliveryMethod, region, effectiveSubtotal, deliveryFee, freeThreshold, onChange,
}: DeliveryCalculatorProps) {
  const { t } = useTranslation();
  const isFree = effectiveSubtotal >= freeThreshold;

  return (
    <div className="space-y-3">
      {/* Home Delivery */}
      <label
        className={`flex gap-4 p-4 border rounded-sm cursor-pointer transition-colors ${
          deliveryMethod === 'delivery'
            ? 'border-primary bg-primary/5'
            : 'border-gray-200 hover:border-primary/40'
        }`}
      >
        <input
          type="radio"
          name="deliveryMethod"
          value="delivery"
          checked={deliveryMethod === 'delivery'}
          onChange={() => onChange('delivery')}
          className="mt-0.5 accent-primary"
        />
        <div className="flex-1">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="material-icons text-xl text-primary">local_shipping</span>
              <p className="font-body font-medium text-sm text-gray-800">
                {t('checkout.home_delivery')}
              </p>
            </div>
            <p className="font-body font-semibold text-sm">
              {isFree ? (
                <span className="text-green-600">{t('common.free')}</span>
              ) : (
                <span>{deliveryFee} AED</span>
              )}
            </p>
          </div>
          <p className="font-body text-xs text-gray-500 mt-1 ml-7">
            {isFree
              ? t('checkout.free_delivery_qualified')
              : t('checkout.delivery_time', { region: t(`regions.${region}`) || 'your address' })}
          </p>
          {!isFree && effectiveSubtotal > 0 && (
            <p className="font-body text-xs text-secondary mt-0.5 ml-7">
              {t('checkout.free_delivery_upsell', {
                amount: (freeThreshold - effectiveSubtotal).toFixed(2),
              })}
            </p>
          )}
        </div>
      </label>

      {/* Store Pickup */}
      <label
        className={`flex gap-4 p-4 border rounded-sm cursor-pointer transition-colors ${
          deliveryMethod === 'pickup'
            ? 'border-primary bg-primary/5'
            : 'border-gray-200 hover:border-primary/40'
        }`}
      >
        <input
          type="radio"
          name="deliveryMethod"
          value="pickup"
          checked={deliveryMethod === 'pickup'}
          onChange={() => onChange('pickup')}
          className="mt-0.5 accent-primary"
        />
        <div className="flex-1">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="material-icons text-xl text-primary">storefront</span>
              <p className="font-body font-medium text-sm text-gray-800">
                {t('checkout.store_pickup')}
              </p>
            </div>
            <p className="font-body font-semibold text-sm text-green-600">
              {t('common.free')}
            </p>
          </div>
          <p className="font-body text-xs text-gray-500 mt-1 ml-7">
            {t('checkout.pickup_description')}
          </p>
        </div>
      </label>
    </div>
  );
}
