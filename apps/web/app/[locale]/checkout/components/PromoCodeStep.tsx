'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { promoApi } from '@/lib/api';
import { pendingVerification } from '@/lib/types';
import { useToast } from '@/components/ui/Toast';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { analytics } from '@/lib/analytics';
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
  identity?: {
    email?: string | null;
    phone?: string | null;
    /** Which kind of order this is. The phone gate only applies to deliveries. */
    delivery_method?: 'delivery' | 'pickup' | null;
  };
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
 */
export function PromoCodeStep({
  promoCode, promoDiscount, promoMessage, subtotal, identity, onChange,
}: PromoCodeStepProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /**
   * Check a code and put the answer on the form.
   *
   * `announced` is false for the automatic re-check below. That one runs
   * whenever the basket or the identity moves, which is every time somebody
   * adds a cake or finishes typing their phone number — so toasting "Applied
   * WELCOME15" from it would fire a success notice at a customer who did
   * nothing, repeatedly, and count each one as a fresh redemption in the
   * analytics. A silent re-check that quietly keeps the number right is the
   * whole point of it.
   */
  const apply = useCallback(async (raw: string, announced = true) => {
    const code = raw.trim().toUpperCase();
    if (!code) return;
    setLoading(true);
    setError(null);
    try {
      const result = await promoApi.validate(code, subtotal, identity);
      if (result.valid) {
        // Valid *and* still owing a proved number is now a real answer: the
        // discount is applied and the SMS is asked for on the address form.
        // Reported up rather than kept here because the pay button is what acts
        // on it, and this panel is folded away behind a toggle most customers
        // never open.
        const pending = pendingVerification(result);
        onChange({
          promoCode: code,
          promoDiscount: Number(result.discount_amount),
          promoMessage: result.message ?? '',
          promoNeedsVerify: pending,
        });
        if (announced) {
          analytics.promoApplied({
            code,
            discount: Number(result.discount_amount),
            surface: 'checkout',
            subtotal,
          });
          addToast(t('checkout.promo_applied', { code }), 'success');
        }
      } else {
        const reason = result.message ?? t('checkout.invalid_promo');
        setError(reason);
        analytics.promoFailed({
          code,
          // Trimmed because the server's refusals are full sentences and a
          // property with one distinct value per incident cannot be grouped.
          reason: reason.slice(0, 60),
          surface: 'checkout',
          subtotal,
        });
        onChange({ promoCode: code, promoDiscount: 0, promoMessage: '', promoNeedsVerify: false });
      }
    } catch {
      // A network failure on a silent re-check leaves the discount alone. The
      // server re-validates at order creation regardless, and dropping a
      // legitimate discount because one background request timed out is the
      // worse of the two failures.
      if (announced) {
        setError(t('checkout.promo_error'));
        analytics.promoFailed({ code, reason: 'network_error', surface: 'checkout', subtotal });
      }
    } finally {
      setLoading(false);
    }
  }, [subtotal, identity, onChange, addToast, t]);

  const handleApply = useCallback(() => apply(promoCode), [apply, promoCode]);

  // An applied discount is priced against a subtotal and judged against an
  // identity, and both move while the customer is still filling the form in —
  // they add a cake, they type their phone. Re-checking keeps the line on the
  // summary equal to what the order will actually be written with, instead of
  // letting it drift until the pay button disagrees with it.
  //
  // Keyed on the values themselves rather than on a timer, and skipped while
  // nothing is applied, so an empty box never talks to the server.
  const appliedRef = useRef(promoDiscount > 0 ? promoCode : '');
  appliedRef.current = promoDiscount > 0 ? promoCode : '';
  useEffect(() => {
    const code = appliedRef.current;
    if (!code) return;
    const timer = setTimeout(() => { void apply(code, false); }, 500);
    return () => clearTimeout(timer);
    // `apply` is deliberately not a dependency: it closes over the same values
    // this effect watches, so including it would re-fire on its own identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subtotal, identity?.email, identity?.phone, identity?.delivery_method]);

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
