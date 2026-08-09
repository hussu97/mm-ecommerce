'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { analytics } from '@/lib/analytics';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import type { AdvertisedPromo } from '@/lib/types';
import { CouponCode } from '@/components/promo/CouponCode';
import { Icon } from '@/components/ui/Icon';

const DISMISS_KEY = 'mm_promo_banner_dismissed';

/**
 * The new-customer offer, said before anybody has decided to buy.
 *
 * Until now the coupon existed only in the basket tray, which meant it was
 * claimed almost entirely by people who were already buying — an acquisition
 * offer spent on customers it did not acquire. This is the first place a
 * shopper who has not added anything yet can learn the offer is there.
 *
 * Every figure comes from the row the checkout will validate against, handed
 * down from the server, so a percentage changed in the admin changes this strip
 * and the tray and the coupon at the same instant. It follows that there is no
 * banner when there is no campaign — a strip advertising a code that
 * `validate` refuses is worse than no strip at all.
 *
 * The promo is a **prop** rather than a fetch of its own. The homepage already
 * asks for it, to state the offer in structured data for crawlers that never
 * run this file, and a second request would buy a duplicate round trip and a
 * flash of empty bar for nothing.
 */
export function PromoBanner({ promo }: { promo: AdvertisedPromo | null }) {
  const { t, locale } = useTranslation();
  const [visible, setVisible] = useState(false);

  // Read after mount, not during render: `sessionStorage` does not exist on the
  // server, and a bar rendered on the server and removed on hydration is a
  // layout shift on the first paint of the homepage.
  useEffect(() => {
    const dismissed = sessionStorage.getItem(DISMISS_KEY);
    if (!dismissed) setVisible(true);
  }, []);

  // The copy here is percentage-shaped, so a fixed-amount coupon gets no strip
  // until there is wording that fits it — see `isAdvertisable` on the homepage,
  // which is the same guard the tray uses.
  const showable =
    promo !== null && promo.discount_type === 'percentage' && promo.first_orders_limit !== null;

  useEffect(() => {
    if (!visible || !showable || !promo) return;
    // Without this the click has no denominator: taps were counted and the
    // homepages that carried the offer and were scrolled past were not.
    analytics.couponBannerShown({
      code: promo.code,
      percent: Number(promo.discount_value),
    });
  }, [visible, showable, promo]);

  if (!visible || !showable || !promo || promo.first_orders_limit === null) return null;

  // The Arabic spelling is a second name for the same row, so either one
  // applies the same coupon. Show the one the page is being read in.
  const code = (locale === 'ar' && promo.code_ar) || promo.code;

  const dismiss = () => {
    sessionStorage.setItem(DISMISS_KEY, '1');
    setVisible(false);
  };

  return (
    <div className="bg-primary text-white text-center text-xs tracking-widest uppercase py-2.5 px-10 relative font-body">
      <Link
        href={`/${locale}/all-products`}
        onClick={() => analytics.couponBannerClicked({ code: promo.code })}
        className="inline-flex items-center gap-2 hover:opacity-90 transition-opacity"
      >
        <Icon name="local_offer" className="text-sm" aria-hidden />
        <span className="inline-flex flex-wrap items-center justify-center gap-x-1.5 gap-y-1">
          <span>
            {t('promo.new_customer_title', {
              percent: promo.discount_value,
              orders: promo.first_orders_limit,
            })}
          </span>
          <span aria-hidden className="opacity-50">·</span>
          <span>{t('promo.use_code')}</span>
          <CouponCode code={code} tone="dark" />
        </span>
      </Link>
      <button
        onClick={dismiss}
        className="absolute end-3 top-1/2 -translate-y-1/2 opacity-70 hover:opacity-100 transition-opacity"
        aria-label={t('promo.dismiss_banner')}
      >
        <Icon name="close" className="text-sm" />
      </button>
    </div>
  );
}
