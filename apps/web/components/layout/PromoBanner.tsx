'use client';

import { useEffect, useState } from 'react';

const DISMISS_KEY = 'mm_promo_banner_dismissed';

export function PromoBanner() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const dismissed = sessionStorage.getItem(DISMISS_KEY);
    if (!dismissed) setVisible(true);
  }, []);

  const dismiss = () => {
    sessionStorage.setItem(DISMISS_KEY, '1');
    setVisible(false);
  };

  if (!visible) return null;

  return (
    <div className="bg-primary text-white text-center text-xs tracking-widest uppercase py-2.5 px-4 relative font-body">
      <span>Free Shipping on orders above 200 AED</span>
      <span className="mx-2 opacity-50">·</span>
      <span>
        Use code{' '}
        <span className="font-semibold border border-white/50 px-1.5 py-0.5 rounded-sm">
          FREESHIP
        </span>
      </span>
      <button
        onClick={dismiss}
        className="absolute right-3 top-1/2 -translate-y-1/2 opacity-70 hover:opacity-100 transition-opacity"
        aria-label="Dismiss promotion banner"
      >
        <span className="material-icons text-sm">close</span>
      </button>
    </div>
  );
}
