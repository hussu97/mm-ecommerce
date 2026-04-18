'use client';

import { useCallback, useState } from 'react';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { promoApi } from '@/lib/api';
import { useToast } from '@/components/ui/Toast';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { analytics } from '@/lib/analytics';

interface PromoCodeStepProps {
  promoCode: string;
  promoDiscount: number;
  promoMessage: string;
  subtotal: number;
  onChange: (patch: { promoCode: string; promoDiscount: number; promoMessage: string }) => void;
}

/**
 * Promo code entry + applied-code display with clear button.
 * Independently testable — accepts subtotal and emits patch to parent form.
 */
export function PromoCodeStep({
  promoCode, promoDiscount, promoMessage, subtotal, onChange,
}: PromoCodeStepProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleApply = useCallback(async () => {
    const code = promoCode.trim().toUpperCase();
    if (!code) return;
    setLoading(true);
    setError(null);
    try {
      const result = await promoApi.validate(code, subtotal);
      if (result.valid) {
        onChange({ promoCode: code, promoDiscount: Number(result.discount_amount), promoMessage: result.message ?? '' });
        analytics.promoApplied({ code, discount: Number(result.discount_amount) });
        addToast(t('checkout.promo_applied', { code }), 'success');
      } else {
        const reason = result.message ?? t('checkout.invalid_promo');
        setError(reason);
        analytics.promoFailed({ code, reason });
        onChange({ promoCode: code, promoDiscount: 0, promoMessage: '' });
      }
    } catch {
      setError(t('checkout.promo_error'));
      analytics.promoFailed({ code, reason: 'network_error' });
    } finally {
      setLoading(false);
    }
  }, [promoCode, subtotal, onChange, addToast, t]);

  const handleRemove = () => {
    onChange({ promoCode: '', promoDiscount: 0, promoMessage: '' });
    setError(null);
  };

  if (promoDiscount > 0) {
    return (
      <div className="flex items-center justify-between bg-green-50 border border-green-200 rounded-sm px-3 py-2 mb-4">
        <div>
          <p className="font-body text-xs font-medium text-green-800">{promoCode}</p>
          {promoMessage && (
            <p className="font-body text-xs text-green-600">{promoMessage}</p>
          )}
        </div>
        <button
          onClick={handleRemove}
          className="text-green-400 hover:text-green-700 transition-colors"
          aria-label={t('checkout.remove_promo')}
        >
          <span className="material-icons text-base">close</span>
        </button>
      </div>
    );
  }

  return (
    <div className="flex gap-2 items-start mb-4">
      <div className="flex-1 min-w-0">
        <Input
          placeholder={t('checkout.promo_placeholder')}
          value={promoCode}
          onChange={(e) => {
            onChange({ promoCode: e.target.value.toUpperCase(), promoDiscount: 0, promoMessage: '' });
            setError(null);
          }}
          onKeyDown={(e) => e.key === 'Enter' && handleApply()}
          error={error ?? undefined}
        />
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleApply}
        loading={loading}
        disabled={!promoCode.trim()}
        className="shrink-0"
      >
        {t('checkout.apply')}
      </Button>
    </div>
  );
}
