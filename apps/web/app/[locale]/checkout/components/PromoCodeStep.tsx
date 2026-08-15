'use client';

import { useCallback, useState } from 'react';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { usePromoValidation, type PromoIdentity } from '@/lib/use-promo-validation';
import { Icon } from '@/components/ui/Icon';

interface PromoCodeStepProps {
  promoCode: string;
  promoDiscount: number;
  promoMessage: string;
  subtotal: number;
  /**
   * Who the checkout thinks this is, so far.
   *
   * Sent with every validation because the server judges a new-customer coupon
   * on the account, the email *and* the phone — and order creation checks all
   * three. Validating without them answers a different question from the one
   * the pay button is about to be judged on: the discount reads as applied, and
   * the order is refused at the last step with nothing the customer can do
   * about it from there.
   */
  identity?: PromoIdentity;
  onChange: (patch: {
    promoCode: string;
    promoDiscount: number;
    promoMessage: string;
    promoNeedsVerify: boolean;
  }) => void;
}

/**
 * Promo code entry + applied-code display with clear button.
 * Independently testable — accepts subtotal and emits patch to parent form.
 *
 * Entry and removal only. The automatic re-check that keeps an applied
 * discount honest lives in `usePromoRevalidation` up in the checkout, not
 * here — this panel is folded away behind an "add promo or note" toggle most
 * customers never open, and a re-check that only runs while a fold-out is
 * open is a re-check that mostly never runs.
 */
export function PromoCodeStep({
  promoCode, promoDiscount, promoMessage, subtotal, identity, onChange,
}: PromoCodeStepProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const { validateCode } = usePromoValidation('checkout');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /**
   * Check a code and put the answer on the form.
   *
   * Valid *and* still owing a proved number is a real answer: the discount is
   * applied and the SMS is asked for on the address form. Reported up rather
   * than kept here because the pay button is what acts on it, and this panel
   * is folded away behind a toggle most customers never open.
   */
  const apply = useCallback(async (raw: string) => {
    setLoading(true);
    setError(null);
    try {
      const outcome = await validateCode(raw, {
        subtotal,
        identity,
        fallbackReason: t('checkout.invalid_promo'),
      });
      if (!outcome) return;
      if (outcome.kind === 'applied') {
        onChange({
          promoCode: outcome.code,
          promoDiscount: outcome.discount,
          promoMessage: outcome.message,
          promoNeedsVerify: outcome.needsVerify,
        });
        addToast(t('checkout.promo_applied', { code: outcome.code }), 'success');
      } else if (outcome.kind === 'invalid') {
        setError(outcome.message ?? t('checkout.invalid_promo'));
        onChange({ promoCode: outcome.code, promoDiscount: 0, promoMessage: '', promoNeedsVerify: false });
      } else {
        setError(t('checkout.promo_error'));
      }
    } finally {
      setLoading(false);
    }
  }, [validateCode, subtotal, identity, onChange, addToast, t]);

  const handleApply = useCallback(() => apply(promoCode), [apply, promoCode]);

  const handleRemove = () => {
    onChange({ promoCode: '', promoDiscount: 0, promoMessage: '', promoNeedsVerify: false });
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
          <Icon name="close" className="text-base" />
        </button>
      </div>
    );
  }

  return (
    <div className="mb-4">
      <div className="flex gap-2 items-start">
        <div className="flex-1 min-w-0">
          <Input
            placeholder={t('checkout.promo_placeholder')}
            value={promoCode}
            onChange={(e) => {
              onChange({ promoCode: e.target.value.toUpperCase(), promoDiscount: 0, promoMessage: '', promoNeedsVerify: false });
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

      {/* No verification panel here any more, and deliberately none.
          It used to live at this spot because a refused code was the only
          signal we had — but the server no longer refuses a coupon over an
          unproved number while the customer is still shopping, so this branch
          was never reached again. An unproved number is now reported as
          outstanding, the discount applies, and the SMS is asked for on the
          address form, where the number is being typed anyway and where a guest
          can actually reach it. This panel is folded away behind an "add promo
          or note" toggle that most customers never open, which is the last
          place a required step should hide. */}
    </div>
  );
}
