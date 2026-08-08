'use client';

import { useEffect, useState } from 'react';
import { promoApi } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import type { AdvertisedPromo } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

interface NewCustomerCouponTrayProps {
  /** The code applied to this basket, if one is. Either spelling counts. */
  appliedCode: string | null;
  /**
   * Applies a code on the caller's behalf. Resolves to an error message to
   * show, or `null` when it went on.
   *
   * The tray does not validate anything itself: the basket, the discount and
   * the toast all belong to the page that owns them, and a second code path for
   * applying a coupon is a second chance for the two to disagree.
   */
  onApply: (code: string) => Promise<string | null>;
}

/**
 * The new-customer offer, laid out where the basket is — so a customer can take
 * it without having been told a code.
 *
 * That is the whole reason this exists. A coupon nobody knows about is claimed
 * by the people who were already going to buy, and typing a code correctly is a
 * step at which shoppers give up. One tap applies it.
 *
 * Every figure comes from `/promo-codes/featured` and none of them are written
 * here. Changing the percentage, the cap or the number of orders in the admin
 * has to change the tray, its terms and the code it applies at the same instant
 * — a tray advertising 15% against a row that grants 10% is worse than no tray.
 * It follows that the tray is absent whenever the API says there is nothing to
 * advertise, including while the request is still in flight.
 *
 * The terms say what the offer is and what it requires. They deliberately do
 * not say how we work out that somebody is a new customer: that is our problem,
 * it names identifiers the customer never gave us for this purpose, and written
 * down it doubles as instructions for getting around it.
 */
export function NewCustomerCouponTray({ appliedCode, onApply }: NewCustomerCouponTrayProps) {
  const { t, locale } = useTranslation();
  const [promo, setPromo] = useState<AdvertisedPromo | null>(null);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    promoApi
      .featured()
      .then((p) => { if (!cancelled) setPromo(p); })
      .catch(() => { /* no campaign we can prove is running, so no tray */ });
    return () => { cancelled = true; };
  }, []);

  if (!promo) return null;
  // The copy this tray has is percentage-shaped ("{percent}% off your first
  // {orders} orders"). A fixed-amount coupon rendered through it would read as
  // "20% off" for a 20-dirham discount, so it gets no tray until there is
  // wording that fits it.
  if (promo.discount_type !== 'percentage' || promo.first_orders_limit === null) return null;

  // The Arabic spelling is a second name for the same row, so either one
  // applies the same coupon. Show the one the page is being read in.
  const code = (locale === 'ar' && promo.code_ar) || promo.code;
  const applied =
    appliedCode !== null &&
    [promo.code, promo.code_ar].some((c) => c && c.toUpperCase() === appliedCode.toUpperCase());

  const terms = [
    promo.max_discount_amount !== null
      ? t('promo.terms_max', { max: promo.max_discount_amount })
      : null,
    t('promo.terms_first_orders', { orders: promo.first_orders_limit }),
    // Only when it is true of this coupon. A term about a step the customer
    // does not have to take is a reason not to bother.
    promo.requires_phone_verification ? t('promo.terms_verify') : null,
    t('promo.terms_one_account'),
    t('promo.terms_limited'),
    t('promo.terms_combine'),
  ].filter((line): line is string => Boolean(line));

  const handleApply = async () => {
    setApplying(true);
    setError(null);
    const message = await onApply(code);
    setError(message);
    setApplying(false);
  };

  return (
    <section
      aria-label={t('promo.new_customer_title', {
        percent: promo.discount_value,
        orders: promo.first_orders_limit,
      })}
      className="rounded-sm border border-secondary/40 bg-secondary/5 p-4 space-y-3"
    >
      <div className="flex items-start gap-3">
        <Icon name="local_offer" className="text-xl text-secondary shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="font-body text-sm font-medium text-gray-900 leading-snug">
            {t('promo.new_customer_title', {
              percent: promo.discount_value,
              orders: promo.first_orders_limit,
            })}
          </p>
          {/* The code itself, spelled out. Some people would rather type it, and
              anyone comparing this against an email or a poster needs to see it. */}
          <p className="font-body text-xs text-gray-500 mt-0.5">
            {t('promo.new_customer_cta', { code })}
          </p>
        </div>
        {applied ? (
          <span className="shrink-0 inline-flex items-center gap-1 font-body text-xs font-medium text-green-700">
            <Icon name="check_circle" className="text-base" />
            {t('promo.new_customer_applied')}
          </span>
        ) : (
          <Button
            variant="primary"
            size="sm"
            onClick={handleApply}
            loading={applying}
            className="shrink-0"
          >
            {t('promo.new_customer_apply')}
          </Button>
        )}
      </div>

      {error && (
        <p role="alert" className="font-body text-xs text-red-600">
          {error}
        </p>
      )}

      <div className="pt-3 border-t border-secondary/20">
        <p className="font-body text-[11px] uppercase tracking-[0.18em] text-gray-400 mb-1.5">
          {t('promo.terms_title')}
        </p>
        <ul className="space-y-1">
          {terms.map((line) => (
            <li key={line} className="font-body text-xs text-gray-500 leading-relaxed ps-3 relative">
              <span aria-hidden className="absolute start-0 top-0">·</span>
              {line}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
