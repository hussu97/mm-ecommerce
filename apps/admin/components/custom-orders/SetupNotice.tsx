'use client';

/**
 * Says why custom orders are off, and where to turn them on.
 *
 * `GET /admin/custom-orders/status` answers only enabled-or-not; the three
 * settings behind it are read from the business settings so the notice can
 * name the one that is missing. If all three are set and the channel is still
 * off, one of them points at something no longer usable (an inactive product,
 * a deleted branch) — the notice says so rather than guessing which.
 */

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { businessSettingsApi } from '@/lib/pos-api';

export function SetupNotice() {
  const [missing, setMissing] = useState<string[] | null>(null);

  useEffect(() => {
    businessSettingsApi
      .get()
      .then(s => {
        const out: string[] = [];
        if (!s.custom_orders_branch_id) out.push('the branch that makes custom orders');
        if (!s.custom_orders_product_id) out.push('the product custom-order lines are sold as');
        if (!s.custom_orders_inventory_category_id) out.push('the inventory category recipes draw from');
        setMissing(out);
      })
      .catch(() => setMissing([]));
  }, []);

  return (
    <div role="status" className="mb-4 border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-body text-amber-900">
      <p className="font-medium">Custom orders aren&rsquo;t set up yet.</p>
      {missing && missing.length > 0 ? (
        <p className="mt-1">Still to choose: {missing.join('; ')}.</p>
      ) : missing ? (
        <p className="mt-1">
          All three settings are filled in, but one of them no longer resolves (an inactive
          product, a deleted branch or category). Check them.
        </p>
      ) : null}
      <Link
        href="/pos-config/custom-orders"
        className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-amber-900 underline underline-offset-2 hover:no-underline"
      >
        Open custom-order settings
        <span className="material-icons text-[16px]">arrow_forward</span>
      </Link>
    </div>
  );
}
