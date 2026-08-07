'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { PhoneVerify } from '@/components/ui/PhoneVerify';
import { promoApi } from '@/lib/api';
import { isFirebaseConfigured } from '@/lib/firebase';
import { useToast } from '@/components/ui/Toast';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { withFallback } from '@/lib/i18n/fallback';
import { analytics } from '@/lib/analytics';

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
  identity?: { email?: string | null; phone?: string | null };
  onChange: (patch: { promoCode: string; promoDiscount: number; promoMessage: string }) => void;
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
  // Set when the *only* thing standing between this customer and the discount
  // is an unverified phone — which is a refusal with a button that fixes it,
  // and so is worth telling apart from every other refusal.
  const [needsVerify, setNeedsVerify] = useState(false);

  const label = useCallback(
    (key: string, english: string) => withFallback(t, key, english),
    [t],
  );

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
    setNeedsVerify(false);
    try {
      const result = await promoApi.validate(code, subtotal, identity);
      if (result.valid) {
        onChange({ promoCode: code, promoDiscount: Number(result.discount_amount), promoMessage: result.message ?? '' });
        if (announced) {
          analytics.promoApplied({ code, discount: Number(result.discount_amount) });
          addToast(t('checkout.promo_applied', { code }), 'success');
        }
      } else {
        const reason = result.message ?? t('checkout.invalid_promo');
        setError(reason);
        setNeedsVerify(Boolean(result.requires_phone_verification));
        analytics.promoFailed({ code, reason });
        onChange({ promoCode: code, promoDiscount: 0, promoMessage: '' });
      }
    } catch {
      // A network failure on a silent re-check leaves the discount alone. The
      // server re-validates at order creation regardless, and dropping a
      // legitimate discount because one background request timed out is the
      // worse of the two failures.
      if (announced) {
        setError(t('checkout.promo_error'));
        analytics.promoFailed({ code, reason: 'network_error' });
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
  }, [subtotal, identity?.email, identity?.phone]);

  const handleRemove = () => {
    onChange({ promoCode: '', promoDiscount: 0, promoMessage: '' });
    setError(null);
    setNeedsVerify(false);
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
    <div className="mb-4">
      <div className="flex gap-2 items-start">
        <div className="flex-1 min-w-0">
          <Input
            placeholder={t('checkout.promo_placeholder')}
            value={promoCode}
            onChange={(e) => {
              onChange({ promoCode: e.target.value.toUpperCase(), promoDiscount: 0, promoMessage: '' });
              setError(null);
              setNeedsVerify(false);
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

      {/* The way out, next to the refusal. Without it the message is a dead end:
          the only other place a number can be verified is the address book,
          which is behind a sign-in — and a guest is precisely who this coupon
          is for. Retries the code itself on success, so the customer does not
          have to work out that they should press Apply again. */}
      {needsVerify && identity?.phone && isFirebaseConfigured() && (
        <div className="mt-2 border-s-2 border-secondary/40 ps-3">
          <p className="font-body text-xs text-gray-500 mb-1.5">
            {label('verify.subtitle', "We'll text you a 6-digit code.")}
          </p>
          <PhoneVerify
            phone={identity.phone}
            onVerified={() => { void apply(promoCode); }}
          />
        </div>
      )}
      {/* No number yet, or a build with no Firebase keys in it — a preview
          deploy, most often. `PhoneVerify` renders nothing at all in that case,
          so without this check the heading above would sit over empty space:
          a customer told a code is coming and no button to ask for one. */}
      {needsVerify && !(identity?.phone && isFirebaseConfigured()) && (
        <p className="mt-2 font-body text-xs text-gray-500">
          {label('verify.enter_phone_first', 'Add your mobile number above, then apply the code again.')}
        </p>
      )}
    </div>
  );
}
