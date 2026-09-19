'use client';

import { useSearchParams } from 'next/navigation';

import { useAuth } from '@/lib/auth-context';

import { windowFromParams } from '../report-window';
import { VatTab } from '../tabs/VatTab';

// The VAT report gates on its own `reports.vat` permission — a different slug from
// the `reports.sales` that opens the rest of Counter Reports — so a manager who
// can see sales but not VAT is stopped here rather than at the (403-ing) API.
export default function VatReportPage() {
  const { user } = useAuth();
  const { window } = windowFromParams(useSearchParams());

  const canView = !!user && (user.is_superadmin || user.permissions.includes('reports.vat'));
  if (!canView) {
    return (
      <p className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        You do not have the <code>reports.vat</code> permission needed to view the VAT report.
      </p>
    );
  }

  return <VatTab window={window} />;
}
